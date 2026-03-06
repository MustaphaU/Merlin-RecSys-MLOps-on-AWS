import numpy as np
import os
import logging
import argparse
import sys
import warnings
import sys
import time
import json

from sklearn import metrics
import pandas as pd

import tritonclient.http as httpclient
import tritonclient.grpc as grpcclient
from tritonclient.utils import *

import boto3

def publish_batch(queue_url, current_batch, pred_label, region_name='us-east-1'):
    sqs_client = boto3.client('sqs', region_name=region_name)
    batch_size  = len(pred_label)
    df = current_batch

    for i in range(batch_size):
        row = df.iloc[i]

        frame = {
            "input0": row[CONTINUOUS_COLUMNS].values.tolist(),
            "input1": row[CATEGORICAL_COLUMNS].values.tolist(),
            "trueval": row['label'],
            "predval": response.as_numpy("OUTPUT0")[i].astype('float64')                                                                                                                 
        }

        payload = json.dumps(frame)

        #send message to SQS
        sqs_client.send_message(QueueUrl=queue_url, MessageBody=payload)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--triton_grpc_url',
                        type=str,
                        required=False,
                        default='localhost:8001',
                        help='URL for Triton gRPC endpoint')
    parser.add_argument('--model_name',
                        type=str,
                        required=False,
                        default='hugectr_dcn_ens',
                        help='Name of the model ensemble to load')
    parser.add_argument('--test_data',
                        type=str,
                        required=False,
                        default='/crit_int_pq/day_1.parquet',
                        help='Path to test data parquet file')
    parser.add_argument('--batch_size',
                        type=int,
                        required=False,
                        default=64,
                        help='Batch size. Max is 64')
    parser.add_argument('--n_batches',
                        type=int,
                        required=False,
                        default=1,
                        help='Number of batches to send')
    parser.add_argument('--queue_url',
                        type=str,
                        required=True,
                        help='URL of SQS queue')
    
    parser.add_argument('--verbose',
                        type=bool,
                        required=False,
                        default=False,
                        help='Verbosity, True or False')
    args = parser.parse_args()

    logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, datefmt='%d-%m-%Y %H:%M:%S')
    logging.info(f"Args: {args}")

    #disable warnings
    if not sys.warnoptions:
        warnings.simplefilter("ignore")

    try:
        triton_client = grpcclient.InferenceServerClient(url=args.triton_grpc_url, verbose=args.verbose)
        logging.info(f"Triton client created.")
        
        triton_client.is_model_ready(args.model_name)
        logging.info(f"Model {args.model_name} is ready!")
    except Exception as e:
        logging.error(f"Channel created failed: {str(e)}")
        sys.exit()

    #load the dataset
    CATEGORICAL_COLUMNS = ['C' + str(i) for i in range(1, 27)]
    CONTINUOUS_COLUMNS = ['I' + str(i) for i in range(1, 14)]
    LABEL_COLUMNS = ['label']
    col_names = CATEGORICAL_COLUMNS + CONTINUOUS_COLUMNS
    col_dtypes = [np.int32] * len(col_names)

    logging.info("Reading dataset...")
    all_batches = pd.read_parquet(args.test_data).tail(args.batch_size*args.n_batches)

    results = []
    with grpcclient.InferenceServerClient(url=args.triton_grpc_url) as client:
        for batch in range(args.n_batches):
            logging.info(f"Requesting inference for batch {batch}...")
            start_idx = batch*args.batch_size
            end_idx = (batch+1)*(args.batch_size)

            current_batch = all_batches[start_idx:end_idx]
            columns = [(col, current_batch[col]) for col in col_names]

            inputs = []
            for i, (name, col) in enumerate(columns):
                d = col.values.astype(col_dtypes[i])
                d = d.reshape(len(d), 1)
                inputs.append(grpcclient.InferInput(name, d.shape, np_to_triton_dtype(col_dtypes[i])))
                inputs[i].set_data_from_numpy(d)

            outputs = []
            outputs.append(grpcclient.InferRequestedOutput("OUTPUT0"))

            response = client.infer(args.model_name, inputs, request_id=str(1), outputs=outputs)
            results.extend(response.as_numpy("OUTPUT0"))

            publish_batch(args.queue_url, current_batch, response.as_numpy("OUTPUT0"))

    logging.info(f"ROC AUC Score: {metrics.roc_auc_score(all_batches[LABEL_COLUMNS].values.tolist(), results)}")