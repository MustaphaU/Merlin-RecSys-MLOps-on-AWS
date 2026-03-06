#!/bin/bash

# Copyright (c) 2021 NVIDIA Corporation. All Rights Reserved.
# Modified by Mustapha Unubi Momoh
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

s3_bucket=$1
data_local=$2
aws_region=$3
data_path=$4
new_data_path=$5

# check if Triton is already deployed
set +e
triton_status=$(helm status triton 2>&1)
set -e
if [[ "$triton_status" == "Error: release: not found" ]]; then
    echo "FIRST RUN: Triton not deployed"
    if [ -d "$data_local" ]; then
        echo "Directory ${data_local} exists. Downloading initial training data..."
        
        # create local directory structure
        mkdir -p $data_local/criteo-data/crit_int_pq
        
        # download parquet files from S3
        echo "Copying from s3://${s3_bucket}/${data_path} to ${data_local}/criteo-data/crit_int_pq/"
        aws s3 cp s3://${s3_bucket}/${data_path} $data_local/criteo-data/crit_int_pq/ --recursive
        [ "$(ls -A $data_local/criteo-data/crit_int_pq/)" ] || { echo "Error: No data found at s3://${s3_bucket}/${data_path}"; exit 1; }
        
        echo "Initial data extraction completed"
        ls -la $data_local/criteo-data/crit_int_pq/ | head -10
    else
        echo "Error: ${data_local} not found. Cannot continue."
        exit 1
    fi
else
    echo "RECURRENT RUN: Triton already deployed"
    
    if [ -d "$data_local" ]; then
        echo "Directory ${data_local} exists. Downloading incremental data..."
        
        # create directory for new data if it not exist/ clean up any existing data
        mkdir -p $data_local/criteo-data/new_data
        rm -rf $data_local/criteo-data/new_data/*

        echo "Downloading from s3://${s3_bucket}/${new_data_path}"
        aws s3 cp s3://${s3_bucket}/${new_data_path} $data_local/criteo-data/new_data/ --recursive
        [ "$(ls -A $data_local/criteo-data/new_data/)" ] || { echo "Error: No data found at s3://${s3_bucket}/${new_data_path}"; exit 1; }
        
        echo "Incremental data extraction completed" 
        ls -la $data_local/criteo-data/new_data/ | head -10
        echo "Saving all s3 paths of the copied data to $data_local/criteo-data/new_data_paths.txt"
        find $data_local/criteo-data/new_data/ -type f -exec basename {} \; | sed "s|^|s3://${s3_bucket}/${new_data_path}/|" > $data_local/criteo-data/new_data_paths.txt
    else
        echo "Error: ${data_local} not found. Cannot continue."
        exit 1
    fi
fi