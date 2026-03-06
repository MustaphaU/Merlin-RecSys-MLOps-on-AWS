# Copyright (c) 2021 NVIDIA Corporation. All Rights Reserved.
# Modified by Mustapha Unubi Momoh for EKS Deployment
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import os
import logging
from time import sleep
from queue import Queue
from threading import Thread
import argparse
import boto3
import json
import collections
from sklearn import metrics
import pandas as pd
import numpy as np
import kfp
import datetime

def get_pipeline_id(name, client):
    pl_id = None
    page_size = 100
    page_token = ''
    while True:
        res = client.list_pipelines(page_size=page_size, page_token=page_token)
        pl_list = res.pipelines
        for pl in pl_list:
            if pl.name == name:
                pl_id = pl.pipeline_id
                return pl_id
        page_token = res.next_page_token
        if not page_token:
            break
    return pl_id    

def get_pipeline_info(pipeline_id, client_key):
    page_size = 200
    page_token = ''
    pipeline_runs = []
    
    client = kfp.Client(host=client_key)
    res = client.list_runs(page_size=page_size, page_token=page_token)
    for runs in res.runs:
        if runs.pipeline_version_reference.pipeline_id == pipeline_id:
            pipeline_runs.append(runs)
    if len(pipeline_runs) != 0:
        for pipeline_run in pipeline_runs:
            if pipeline_run.state == 'RUNNING':
                return None
        tmp = {
            'pipelineID': pipeline_run.pipeline_version_reference.pipeline_id,
            'versionID': pipeline_run.pipeline_version_reference.pipeline_version_id,
            'experimentID': pipeline_run.experiment_id,
            'status': pipeline_run.state,
            'new_run_name': 'triggered_' + datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        }
        return tmp
    return None

def trigger_kfp(pipeline_id, client_key,service_account, pipeline_params):
    logging.warning("Triggering Kubeflow pipeline...")

    try:
        pipeline_info = get_pipeline_info(pipeline_id, client_key)
    except Exception as e:
        logging.error(f"Pipeline trigger failed: {e}")
        return False
    logging.info(f"Pipeline info: {pipeline_info}")

    if pipeline_info is not None:
        print(f"Using pipeline ID: {pipeline_info['pipelineID']}\nTriggering: {pipeline_info['new_run_name']}\nat: {datetime.datetime.now()}")
        client = kfp.Client(host=client_key)
        res = client.run_pipeline(experiment_id = pipeline_info['experimentID'],
                                  job_name = pipeline_info['new_run_name'],
                                  pipeline_id=pipeline_info['pipelineID'],
                                  version_id=pipeline_info['versionID'],
                                  service_account=service_account,
                                  params=pipeline_params,
                                 enable_caching = False)
        return True
    else:
        logging.info("Skipping trigger")
        return False

class AccMonitor:
    def __init__(self, aws_region, sqs_queue_url, evaluate_period=500, acc_threshold=0.61,
                 min_trigger_len=0.5, pipeline_id=None, min_log_length=320, log_time_delta=60,
                 local_data_dir='/var/lib/data', client_host_key=None, pipeline_params=None, service_account=None):
        self.evaluate_period = evaluate_period
        self.pipeline_id = pipeline_id
        self.local_data_dir = local_data_dir
        self.client_host_key = client_host_key
        self.pipeline_params = pipeline_params
        self.service_account = service_account

        #thread safe queues where each item is a request
        self.request_queue = Queue(maxsize=self.evaluate_period)
        self.aws_region = aws_region
        self.sqs_queue_url = sqs_queue_url
        self.acc_threshold = acc_threshold

        #minimum number of results in the circular buffer to initiate a monitoring based trigger
        self.min_trigger_len = min_trigger_len * self.evaluate_period
        self.min_log_length = min_log_length
        self.log_time_delta = datetime.timedelta(seconds=log_time_delta)

        #circular buffer to store results in a rolling window fashion
        self.label_queue = collections.deque(maxlen=self.evaluate_period)
        self.pred_queue = collections.deque(maxlen=self.evaluate_period)

    def run(self):
        def enqueue_request(self):
            """
            Receives messages from SQS queue and adds the request to a queue.

            Decouples message processing from message receiving.
            """
            sqs_client = boto3.client('sqs', region_name=self.aws_region)

            def callback(message):

                payload = json.loads(message['Body'])

                self.request_queue.put(payload)
                sqs_client.delete_message(
                    QueueUrl=self.sqs_queue_url,
                    ReceiptHandle=message['ReceiptHandle']
                )
            logging.info(f"Listening for messages on {self.sqs_queue_url}..\n")
            while True:
                try:
                    #long polling for messages (20 seconds)
                    response = sqs_client.receive_message(
                        QueueUrl=self.sqs_queue_url,
                        MaxNumberOfMessages=10,
                        WaitTimeSeconds=20,
                        MessageAttributeNames=['All']
                    )
                    if 'Messages' in response:
                        for message in response['Messages']:
                            callback(message) #process each message
                                                
                except Exception as e:
                    logging.error(f"Error receiving messages from SQS: {e}")
                    sleep(5) #backoff before retrying

        #start the enqueue thread as a daemon
        enqueue = Thread(target=enqueue_request, args=(self,))
        enqueue.daemon = True
        enqueue.start()

        """
        Fetches request from queue, calcuates rolling accuracy over last N requests.
        If rolling accuracy is below threshold, triggers retraining.
        Saves requests to efs for future training
        """

        rolling_acc = 1.0

        CATEGORICAL_COLUMNS = ['C' + str(x) for x in range(1, 27)]
        CONTINUOUS_COLUMNS = ['I' + str(x) for x in range(1, 14)]
        LABEL_COLUMNS = ['label']
        col_names = LABEL_COLUMNS + CONTINUOUS_COLUMNS + CATEGORICAL_COLUMNS
        DATETIME_FORMAT = '%d_%m_%Y-%H-%M-%S'
        last_log_time = datetime.datetime.strptime('01_01_1970-00-00-00', DATETIME_FORMAT)

        # create an empty dataframe
        df_temp = pd.DataFrame(columns=col_names)

        # ensure temp_storage directory exists
        os.makedirs(os.path.join(self.local_data_dir, "temp_storage"), exist_ok=True)

        while True:
            while self.request_queue.empty():
                sleep(0.1) #wait for request to be added to the queue
            payload = self.request_queue.get() #this does not block since we checked if the queue was empty

            request = np.concatenate((np.array([payload["trueval"]], float),
                                        np.array(payload["input0"]),
                                        np.array(payload["input1"])))
            
            # append new request to the dataframe
            df_temp = pd.concat([df_temp, pd.DataFrame([request], columns=col_names)], ignore_index=True)

            # write to EFS if enough samples collected and 'enough' time has passed since last write.
            # TOFIX: This is problematic if no new request comes for a while and
            # there are many requests in the dataframe ready to be written already. Currently 320 samples and 60 secs
            current_time = datetime.datetime.now()
            if (df_temp.shape[0] >= self.min_log_length) and \
                (current_time - last_log_time >= self.log_time_delta):
                filename = current_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ") + ".parquet"
                logging.info(f"Writing {df_temp.shape[0]} records to {self.local_data_dir}/temp_storage/{filename}...") 

                df_temp.reset_index(inplace=True, drop=True)
                df_temp.to_parquet(os.path.join(self.local_data_dir, "temp_storage", filename)) #full path is /var/lib/data/temp_storage

                #clear the dataframe
                df_temp = pd.DataFrame(columns=col_names)
                last_log_time = current_time

            # circular buffer of size evaluate_period
            self.label_queue.append(payload["trueval"])
            self.pred_queue.append(payload["predval"])

            try:
                # calculate rolling AUC score
                rolling_acc = metrics.roc_auc_score(self.label_queue, self.pred_queue)
                logging.info(f"Rolling AUC score: {rolling_acc:.4f}")
            except Exception as e:
                logging.warning(f"Error calculating AUC score: {e}")
                pass

            #trigger pipeline if accuracy drops below threshold
            if (rolling_acc < self.acc_threshold) and (len(self.label_queue) > self.min_trigger_len):
                logging.warning(f"Rolling AUC score {rolling_acc:.4f} is below threshold {self.acc_threshold}. Triggering pipeline...")
                success = trigger_kfp(self.pipeline_id, self.client_host_key, self.service_account, self.pipeline_params)

                if success:
                    self.label_queue.clear()
                    self.pred_queue.clear()
                    rolling_acc = 1.0 #reset rolling acc after trigger to avoid multiple triggers in a row
                    sleep(5)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--aws_region",
                        type=str,
                        required=True,
                        default="us-east-1",
                        help="AWS region")
    parser.add_argument("--sqs_queue_url",
                        type=str,
                        required=True,
                        help="SQS Queue URL for inference requests")
    parser.add_argument("--evaluate_period",
                        type=int,
                        required=False,
                        default=500,
                        help="Evaluate over the last evaluate_period samples")
    parser.add_argument("--min_trigger_len",
                        type=float,
                        required=False,
                        default=0.5,
                        help="Minimum samples in queue before trigger (% of evaluate_period)")
    parser.add_argument("--acc_threshold",
                        type=float,
                        required=False,
                        default=0.61,
                        help="AUC ROC theshold for trigger")
    parser.add_argument("--pipeline_id",
                        type=str,
                        required=True,
                        default='1234567890abcdef',
                        help="ID of Kubeflow pipeline to use as template for triggered runs")
    parser.add_argument("--min_log_length",
                        type=int,
                        required=False,
                        default=320,
                        help="Minimum number of requests per parquet file written to PV (EFS)")
    parser.add_argument("--log_time_delta",
                        type=int,
                        required=False,
                        default=60,
                        help="Minimum time delta (seconds) between parquet files written to PV (EFS)")
    parser.add_argument("--local_data_dir",
                        type=str,
                        required=False,
                        default='/var/lib/data',
                        help="Base path to write new parquet files on PV (EFS)")
    parser.add_argument("--pipeline_params",
                        type=str,
                        required=True,
                        default='{}',
                        help="JSON string of parameters to pass to the pipeline")
    parser.add_argument("--service_account",
                    type=str,
                    required=False,
                    default=None,
                    help="Kubernetes service account to use for the pipeline run")

    args = parser.parse_args()
    pipeline_params = json.loads(args.pipeline_params)

    logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')
    logging.info(f"Args: {args}")

    logging.info("Starting accuracy monitor...")
    client_host_key = None
    try:
        with open('/script/kfp_client_host_key.txt', 'r') as f:
            client_host_key = f.read().strip()
    except FileNotFoundError:
        logging.warning("KFP client host key file not found. Pipeline triggering may not work.")   

    acc_monitor = AccMonitor(aws_region=args.aws_region,
                                sqs_queue_url=args.sqs_queue_url,
                                evaluate_period=args.evaluate_period,
                                acc_threshold=args.acc_threshold,
                                min_trigger_len=args.min_trigger_len,
                                pipeline_id=args.pipeline_id,
                                min_log_length=args.min_log_length,
                                log_time_delta=args.log_time_delta,
                                local_data_dir=args.local_data_dir,
                                client_host_key=client_host_key,
                                pipeline_params=pipeline_params,
                                service_account=args.service_account)
    acc_monitor.run()