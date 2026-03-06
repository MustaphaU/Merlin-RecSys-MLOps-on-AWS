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

MODELS_DIR=${1:-"/model/models"}

echo "Starting Triton Inference Server"
echo "Models directory: $MODELS_DIR"

set -m

# Start Triton server with HugeCTR backend configuration
tritonserver \
    --model-repository=$MODELS_DIR \
    --backend-config=hugectr,hugectr_dcn=$MODELS_DIR/hugectr_dcn/1/hugectr_dcn.json \
    --backend-config=hugectr,supportlonglong=true \
    --model-control-mode=poll \
    --repository-poll-secs=10 \
    --log-verbose=1 &

# Wait for Triton server to initialize
echo "Waiting for Triton server to initialize..."
sleep 120

# Load the ensemble model
echo "Loading DCN ensemble model..."
python3 /model/inference/load-triton-ensemble.py \
    --triton_grpc_url localhost:8001 \
    --model_name hugectr_dcn_ens \
    --verbose False

# Bring Triton server to foreground
echo "Triton server ready, bringing to foreground..."
fg %1