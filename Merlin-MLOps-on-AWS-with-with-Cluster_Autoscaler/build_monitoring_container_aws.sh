#!/bin/bash

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

set -e

AWS_ACCOUNT_ID=$1
AWS_REGION=$2
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
REPO_NAME="merlin/monitoring"
IMAGE_TAG="0.5.1"
FULL_IMAGE_NAME="${ECR_REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"

# login to ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY

# create ECR repository if it doesn't exist
aws ecr describe-repositories --repository-names $REPO_NAME --region $AWS_REGION >/dev/null 2>&1 || \
aws ecr create-repository --repository-name $REPO_NAME --region $AWS_REGION

# build the container
docker build -t $FULL_IMAGE_NAME -f Dockerfile.monitoring .

# push to ECR
echo "Pushing to ECR..."
docker push $FULL_IMAGE_NAME

# store the digest of the pushed image
# mkdir -p .digests
# DIGEST=$(docker inspect --format="{{index .RepoDigests 0}}" "${FULL_IMAGE_NAME}")
# echo "$DIGEST" > .digests/monitoring.txt
# echo "Digest saved to .digests/monitoring.txt for monitoring module use."

# store the image URI for use in kubeflow pipelines
mkdir -p .image_uris
echo "$FULL_IMAGE_NAME" > .image_uris/monitoring.txt
echo "Image URI saved to .image_uris/monitoring.txt for monitoring module."
