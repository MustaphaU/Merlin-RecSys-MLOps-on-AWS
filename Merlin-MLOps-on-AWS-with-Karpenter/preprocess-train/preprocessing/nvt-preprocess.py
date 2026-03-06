# Copyright (c) 2021 NVIDIA Corporation. All Rights Reserved.
# Modified by Mustapha Unubi Momoh for Amazon EKS Deployment
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
import re
import shutil
import warnings
import argparse
import logging

# External Dependencies
import numpy as np
import cudf
import dask_cudf
from dask_cuda import LocalCUDACluster
from dask.distributed import Client
from dask.utils import parse_bytes
import rmm

# NVTabular
import nvtabular as nvt
from nvtabular.ops import Categorify, Clip, FillMissing, Normalize, get_embedding_sizes
from nvtabular.utils import _pynvml_mem_size, device_mem_size


def run_preprocessing(input_path, base_dir, num_train_days, num_val_days, gpu_ids):
    #define paths to save artifacts
    dask_workdir = os.path.join(base_dir, "dask_preprocessed/workdir")
    output_path = os.path.join(base_dir, "dask_preprocessed/output")
    stats_path = os.path.join(base_dir, "dask_preprocessed/stats")

    logging.info(f"Dask Workdir: {dask_workdir}")
    logging.info(f"Output Path: {output_path}")

    #create clean directories for Dask workdir, stats, and output
    for dir_path in [dask_workdir, stats_path, output_path]:
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path)
        os.makedirs(dir_path)
        logging.info(f"created {dir_path}")

    fname = 'day_{}.parquet'
    num_days = len([i for i in os.listdir(input_path) if re.match(fname.format('[0-9]{1,2}'), i) is not None])
    train_paths = [os.path.join(input_path, fname.format(day)) for day in range(num_train_days)]
    valid_paths = [os.path.join(input_path, fname.format(day)) for day in range(num_train_days, num_train_days + num_val_days)]

    logging.info(f"Training data: {train_paths}")
    logging.info(f"Validation data: {valid_paths}")
    
    protocol = "tcp"
    visible_devices = ",".join([str(n) for n in gpu_ids]) #detect devices to place workers (0 GPU here)
    device_limit_frac = 0.4
    device_pool_frac = 0.5
    part_mem_frac = 0.05

    #use total device size to calculate the args.device_limit_frac
    device_size = device_mem_size(kind="total")
    part_size = int(part_mem_frac * device_size)
    logging.info(f"Partition size: {part_size}")

    #OPTIONAL (not needed if gpu_ids < 2)
    # Deploy Dask distributed cluster only if asked for multiple GPUs
    if len(gpu_ids) > 1:
        device_limit = int(device_limit_frac * device_size)
        device_pool_size = int(device_pool_frac * device_size)

        logging.info("Checking if any device memory is already occupied....")
        for dev in visible_devices.split(","):
            fmem = _pynvml_mem_size(kind="free", index=int(dev))
            used = (device_size - fmem) / 1e9
            if used > 1.0:
                warnings.warn(f"BEWARE - {used} GB is already occupied on device {int(dev)}!")
        
        cluster = None
        if cluster is None:
            cluster = LocalCUDACluster(
                protocol = protocol,
                n_workers = len(visible_devices.split(",")),
                CUDA_VISIBLE_DEVICES = visible_devices,
                device_memory_limit = device_limit,
                local_directory=dask_workdir
            )

        logging.info("create the distributed client...")
        client = Client(cluster)

        logging.info("Initialize memory (RMM) pools on all workers..")
        def _rmm_pool():
            rmm.reinitialize(
                #RMM may require the pool size to be multiple of 256
                pool_allocator=True,
                initial_pool_size=(device_pool_size // 256) * 256,
            )

        client.run(_rmm_pool)

    
    #preprocessing
    CONTINUOUS_COLUMNS = ['I' + str(x) for x in range(1,14)]
    CATEGORICAL_COLUMNS = ['C' + str(x) for x in range(1,27)]
    LABEL_COLUMNS = ['label']
    COLUMNS = CONTINUOUS_COLUMNS + CATEGORICAL_COLUMNS + LABEL_COLUMNS

    cat_features = CATEGORICAL_COLUMNS >> Categorify(out_path=stats_path)
    cont_features = CONTINUOUS_COLUMNS >> FillMissing() >> Clip(min_value=0) >> Normalize()
    features = cat_features + cont_features + LABEL_COLUMNS

    logging.info("Defining a workflow object..")
    if len(gpu_ids) > 1:
        workflow=nvt.Workflow(features, client=client)
    else:
        workflow=nvt.Workflow(features)

    dict_dtypes={}
    for col in CATEGORICAL_COLUMNS:
        dict_dtypes[col] = np.int64

    for col in CONTINUOUS_COLUMNS:
        dict_dtypes[col] = np.float32

    for col in LABEL_COLUMNS:
        dict_dtypes[col] = np.float32


    train_dataset = nvt.Dataset(train_paths, engine='parquet', part_size=part_size)
    valid_dataset = nvt.Dataset(valid_paths, engine='parquet', part_size=part_size)

    output_train_dir = os.path.join(output_path, 'train/')
    output_valid_dir = os.path.join(output_path, 'valid/')

    for output_directory in [output_train_dir, output_valid_dir]:
        dir_name = os.path.basename(os.path.normpath(output_directory))
        if not os.path.exists(output_directory):
            os.makedirs(output_directory)
            logging.info(f"creating {dir_name} directory at: {output_directory}")

    logging.info("Workflow fit")
    workflow.fit(train_dataset)

    logging.info("Transform Training data..")
    workflow.transform(train_dataset).to_parquet(output_path=output_train_dir,
                                                 shuffle=nvt.io.Shuffle.PER_PARTITION,
                                                 dtypes=dict_dtypes,
                                                 cats=CATEGORICAL_COLUMNS,
                                                 conts=CONTINUOUS_COLUMNS,
                                                 labels=LABEL_COLUMNS)
    
    logging.info("Transform Validation data..")
    workflow.transform(valid_dataset).to_parquet(output_path=output_valid_dir,
                                                 dtypes=dict_dtypes,
                                                 cats=CATEGORICAL_COLUMNS,
                                                 conts=CONTINUOUS_COLUMNS,
                                                 labels=LABEL_COLUMNS)
    
    #cardinalities list to use in "slot_size_array" in the HUgeCTR training "dcn_parquet.json"
    cardinalities = []
    for col in CATEGORICAL_COLUMNS:
        cardinalities.append(nvt.ops.get_embedding_sizes(workflow)[col][0])

    logging.info(f"cardinalities for configuring slot_size_array: {cardinalities}")
    logging.info(f"saving workflow object at: {output_path + '/workflow'}")
    workflow.save(output_path + '/workflow')

    logging.info("Done")

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('-d',
                        '--input_data_dir',
                        type=str,
                        required=False,
                        default='/crit_int_pq',
                        help='Path for training data dir. Default is /crit_int_pq')

    parser.add_argument('-o',
                        '--output_dir',
                        type=str,
                        required=False,
                        default='/var/lib/data/criteo-data/',
                        help='Path for Output directory. It will create a directory "dask_preprocessed" to store artifacts. Default is /var/lib/data/criteo-data/')

    parser.add_argument('-t',
                        '--n_train_days',
                        type=int,
                        required=False,
                        default=1,
                        help='Number of Criteo data days to use for training dataset. Default is 1. Keep n_train_days + n_val_days<=24')

    parser.add_argument('-v',
                        '--n_val_days',
                        type=int,
                        required=False,
                        default=1,
                        help='Number of Criteo data days to take for validation set after n_train_days. Default is 1. Keep n_train_days + n_val_days<=24.')

    parser.add_argument('-g',
                        '--gpu_ids',
                        nargs='+',
                        type=int,
                        required=False,
                        default=[0],
                        help='List of GPU devices to use for Preprocessing. Default is [0]')

    args = parser.parse_args()

    logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, datefmt='%d-%m-%y %H:%M:%S')

    logging.info(f"Args: {args}")

    run_preprocessing(input_path=args.input_data_dir,
                    base_dir=args.output_dir,
                    num_train_days=args.n_train_days,
                    num_val_days=args.n_val_days,
                    gpu_ids=args.gpu_ids)