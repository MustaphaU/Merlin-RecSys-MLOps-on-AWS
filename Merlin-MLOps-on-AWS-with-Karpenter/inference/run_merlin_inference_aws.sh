#!/bin/bash

# Copyright (c) 2021 NVIDIA Corporation. All Rights Reserved.
# Modified by Mustapha Unubi Momoh for Amazon EKS Deployment
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

PV_LOC=${1:-"/var/lib/data"}
AWS_ACCOUNT_ID=${2:-"123456789012"}
AWS_REGION=${3:-"us-east-1"}
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Create inference directory if it doesn't exist
if ! [ -d $PV_LOC/inference ]; then
    mkdir -p $PV_LOC/inference
    echo "Created inference directory at $PV_LOC/inference"
fi

# Check if Triton is already deployed
triton_status=$(helm status triton 2>&1)
echo "Triton status check: "
echo "$triton_status"

if [[ "$triton_status" == "Error: release: not found" ]]; then
    echo "Triton is not running. Deploying new instance..."
    
    cp /script/inference/load-triton-ensemble.py $PV_LOC/inference/load-triton-ensemble.py
    cp /script/inference/triton/run_triton.sh $PV_LOC/inference/run_triton.sh
    
    TRITON_IMAGE="$ECR_REGISTRY/merlin/inference:0.5.1"
    echo "Deploying Triton with image: $TRITON_IMAGE"
    
    helm install triton /script/inference/triton/ \
        --set image.imageName=$TRITON_IMAGE

else
    echo "Triton is already running, not deploying another instance."
fi
