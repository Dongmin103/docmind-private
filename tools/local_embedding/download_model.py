"""Pre-cache pinned GTE weights and its separately pinned model implementation."""
import argparse
import os
from pathlib import Path

MODEL_REVISION = '9bbca17d9273fd0d03d5725c7a4b0f6b45142062'
CODE_REVISION = '40ced75c3017eb27626c9d4ea981bde21a2662f4'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir',default='/models/huggingface')
    args=parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='0'
    os.environ['TRANSFORMERS_OFFLINE']='0'
    from huggingface_hub import snapshot_download
    cache=Path(args.cache_dir)/'hub'
    model=snapshot_download('Alibaba-NLP/gte-multilingual-base',revision=MODEL_REVISION,cache_dir=str(cache),
        allow_patterns=['*.json','*.safetensors','*.txt','*.model'])
    snapshot_download('Alibaba-NLP/new-impl',revision=CODE_REVISION,cache_dir=str(cache),allow_patterns=['*.py','*.json'])
    # The one-time download runs as root; the service runs as uid/gid 10001.
    # Dynamic model-module caching must remain writable by that service user.
    if os.name!='nt' and os.geteuid()==0:
        for path in [Path(args.cache_dir),*Path(args.cache_dir).rglob('*')]:
            os.chown(path,10001,10001,follow_symlinks=False)
    print('Pinned GTE model and implementation cached:',model)

if __name__=='__main__':main()
