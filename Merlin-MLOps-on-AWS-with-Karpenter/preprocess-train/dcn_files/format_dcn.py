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

import json, sys, argparse, os
import nvtabular as nvt
from nvtabular.ops import get_embedding_sizes
import logging
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, datefmt='%d-%m-%y %H:%M:%S')


if __name__=='__main__':
    
    parser = argparse.ArgumentParser()    

    parser.add_argument("--model_version",
                        type=int,
                        required=True,
                        default=1,
                        help="Provide model version")

    parser.add_argument("--dcn_path",
                        type=str,
                        required=True,
                        default="/var/lib/data/script/dcn_files/dcn.json",
                        help="Path of original DCN")

    parser.add_argument("--workflow_path",
                        type=str,
                        required=True,
                        help="Path of NVTabular workflow (it is needed to extract slot sizes)") 


    args = parser.parse_args()

 # Load workflow to get the slot sizes (cardinalities)
    workflow = nvt.Workflow.load(args.workflow_path)
    embeddings = get_embedding_sizes(workflow)

    # extract cardinalities list to use in "slot_size_array"
    categorical_cols = [f'C{x}' for x in range(1,27)]
    slot_size_array = [embeddings[col][0] for col in categorical_cols]

    logging.info(f"slot_size_array: {slot_size_array}")

    dcn = os.path.basename(args.dcn_path)
    dir_path = os.path.dirname(args.dcn_path)
    obj = None
    with open(args.dcn_path, "r") as f:
        obj = json.load(f)
    obj["inference"]["dense_model_file"] = "/model/models/hugectr_dcn/" + str(args.model_version) + "/_dense_10000.model"
    obj["inference"]["sparse_model_file"] = "/model/models/hugectr_dcn/" + str(args.model_version) + "/0_sparse_10000.model"
    #update the max_vocabulary_size_per_gpu in the json file
    max_vocab_size = int(sum(slot_size_array) * 1.2)
    obj["layers"][1]["sparse_embedding_hparam"]["max_vocabulary_size_per_gpu"] = max_vocab_size

    #add slot_size_array to the top level
    obj["layers"][0]["slot_size_array"] = slot_size_array
    #add slot_size_array to the data layer
    obj["layers"][0]["sparse"][0]["slot_size_array"] = slot_size_array
    updated_json = dir_path+"/hugectr_dcn" + str(args.model_version) + ".json"
    with open(updated_json,"w") as f:
        json.dump(obj, f)

    