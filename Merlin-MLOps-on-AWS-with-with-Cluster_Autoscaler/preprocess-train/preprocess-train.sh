#!/bin/bash

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

export PV_LOC=${1:-"/var/lib/data"}
AWS_REGION=${2:-"us-east-1"}
s3_bucket=${3:-"aws-nvidia-recsys"}
new_data_path=${4:-"new_data_path_in_s3"}
#EKS_CLUSTER=${3:-"my-merlin-cluster"}

# copy script folder to PV_LOC
cp -r /script $PV_LOC
cd $PV_LOC
echo "Working directory: $PV_LOC"


# Check if triton is deployed to determine initial or recurrent run
set +e # allow command to fail without exiting the script
triton_status=$(helm status triton 2>&1)
set -e # exit on any subsequent command failure
echo "Triton status check:"
echo "$triton_status"
if [[ "$triton_status" == *"Error: release: not found"* ]]; then
    echo "Triton is not running. This is the first deployment - running the full preprocessing and training pipeline"
    echo "Preprocessing with NVTabular...."
    CRITEO_DATA_PATH="$PV_LOC/criteo-data/crit_int_pq"
    echo "Preprocessing data from: $CRITEO_DATA_PATH the contents are:"
    ls -al $CRITEO_DATA_PATH

    # -t represent the number of days to be used for training
    # -v represent the number of days to be used for validation (after the training days e.g. if -t 1 -v 1 then day 1 is used for training and day 2 for validation)
    # -g represent the space separated gpu ids to be used for preprocessing (e.g. -g 0 1 2 3 for 4 gpus)
    python3 -u $PV_LOC/script/preprocessing/nvt-preprocess.py \
        -d $CRITEO_DATA_PATH \
        -o $PV_LOC/criteo-data/ \
        -t 1 -v 1 -g 0

    # Initial training
    echo "Initial training with HugeCTR..."
    python3 -u $PV_LOC/script/training/hugectr-train-criteo-dcn.py \
        --input_train $PV_LOC/criteo-data/dask_preprocessed/output/train/_file_list.txt \
        --input_val $PV_LOC/criteo-data/dask_preprocessed/output/valid/_file_list.txt \
        --max_iter 20000 \
        --snapshot 10000 \
        --gpu_ids 0

    # organize trained model files  
    mkdir -p $PV_LOC/model/hugectr_dcn/1/

    #format and copy the model config file (dcn.json) before creating ensemble
    python3 -u $PV_LOC/script/dcn_files/format_dcn.py \
        --model_version 1 \
        --dcn_path $PV_LOC/script/dcn_files/dcn.json \
        --workflow_path $PV_LOC/criteo-data/dask_preprocessed/output/workflow/

    cp $PV_LOC/script/dcn_files/hugectr_dcn1.json $PV_LOC/model/hugectr_dcn/1/hugectr_dcn.json
    
    mv $PV_LOC/*.model $PV_LOC/model/hugectr_dcn/1/

    #create the models directory for Triton
    mkdir -p $PV_LOC/models/

    echo "create ensemble"
    python3 -u $PV_LOC/script/training/create-nvt-hugectr-ensemble.py \
        --nvt_workflow_path $PV_LOC/criteo-data/dask_preprocessed/output/workflow/ \
        --hugectr_model_path $PV_LOC/model/hugectr_dcn/1/ \
        --ensemble_output_path $PV_LOC/models/ \
        --ensemble_config $PV_LOC/script/training/ensemble-config.json

    # cp $PV_LOC/script/dcn_files/dcn.json $PV_LOC/models/dcn/1
else
    echo "Triton is running. This is triggered run. Running incremental pre-processing"
    echo "Incremental preprocessing..."
    ls -al $PV_LOC/criteo-data/new_data

    python3 -u $PV_LOC/script/preprocessing/nvt-preprocess-incremental.py \
        --input_data_dir $PV_LOC/criteo-data/new_data/ \
        --output_dir $PV_LOC/criteo-data/dask_preprocessed/output \
        --workflow_dir $PV_LOC/criteo-data/dask_preprocessed/output/workflow/ \
        --dask_workdir $PV_LOC/criteo-data/dask_preprocessed/workdir \
        --split_ratio 0.7 \
        --gpu_ids 0 

    previous_version=$(ls $PV_LOC/model/hugectr_dcn/ -v | tail -n1)

    echo "Incremental Training..."
    python3 -u $PV_LOC/script/training/hugectr-train-criteo-dcn.py \
        --input_train $PV_LOC/criteo-data/dask_preprocessed/output/train/_file_list.txt \
        --input_val $PV_LOC/criteo-data/dask_preprocessed/output/valid/_file_list.txt \
        --max_iter 20000 \
        --snapshot 10000 \
        --gpu_ids 0 \
        --dense_model_file $PV_LOC/model/hugectr_dcn/$previous_version/_dense_10000.model \
        --sparse_model_files $PV_LOC/model/hugectr_dcn/$previous_version/0_sparse_10000.model

    new_version="$(($previous_version + 1))" 

    python3 -u $PV_LOC/script/dcn_files/format_dcn.py \
        --model_version $new_version \
        --dcn_path $PV_LOC/script/dcn_files/dcn.json \
        --workflow_path $PV_LOC/criteo-data/dask_preprocessed/output/workflow/
    
    mkdir -p $PV_LOC/model/hugectr_dcn/$new_version/
    cp $PV_LOC/script/dcn_files/hugectr_dcn$new_version.json $PV_LOC/model/hugectr_dcn/$new_version/hugectr_dcn.json
    mv $PV_LOC/*.model $PV_LOC/model/hugectr_dcn/$new_version/


    mkdir -p $PV_LOC/models_recurrent_runs

    echo "Incremental Create ensemble"
    python3 -u $PV_LOC/script/training/create-nvt-hugectr-ensemble.py \
        --nvt_workflow_path $PV_LOC/criteo-data/dask_preprocessed/output/workflow/ \
        --hugectr_model_path $PV_LOC/model/hugectr_dcn/$new_version/ \
        --ensemble_output_path $PV_LOC/models_recurrent_runs \
        --ensemble_config $PV_LOC/script/training/ensemble-config.json


    mv $PV_LOC/models_recurrent_runs/hugectr_dcn/1 $PV_LOC/models/hugectr_dcn/$new_version
    mv $PV_LOC/models_recurrent_runs/hugectr_dcn/config.pbtxt $PV_LOC/models/hugectr_dcn/

    mv $PV_LOC/models_recurrent_runs/hugectr_dcn_ens/1 $PV_LOC/models/hugectr_dcn_ens/$new_version
    mv $PV_LOC/models_recurrent_runs/hugectr_dcn_ens/config.pbtxt $PV_LOC/models/hugectr_dcn_ens/

    mv $PV_LOC/models_recurrent_runs/hugectr_dcn_nvt/1 $PV_LOC/models/hugectr_dcn_nvt/$new_version
    mv $PV_LOC/models_recurrent_runs/hugectr_dcn_nvt/config.pbtxt $PV_LOC/models/hugectr_dcn_nvt/

    rm -rf $PV_LOC/models_recurrent_runs

    echo "moving the training data files in S3 to an archive..."
    while read s3_path; do
        key=${s3_path#s3://$s3_bucket/}
        archive_key=${key/$new_data_path/archived_data}
        aws s3 mv "$s3_path" "s3://$s3_bucket/$archive_key"
    done < $PV_LOC/criteo-data/new_data_paths.txt

fi