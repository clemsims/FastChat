'''
Downloads models from Hugging Face to models/model-name.

Example:
python download-model.py facebook/opt-1.3b

'''

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import requests
import tqdm
from tqdm.contrib.concurrent import thread_map

parser = argparse.ArgumentParser()
parser.add_argument('MODEL', type=str, default=None, nargs='?')
parser.add_argument('--branch', type=str, default='main',
                    help='Name of the Git branch to download from.')
parser.add_argument('--threads', type=int, default=1,
                    help='Number of files to download simultaneously.')
parser.add_argument('--text-only', action='store_true',
                    help='Only download text files (txt/json).')
parser.add_argument('--output', type=str, default=None,
                    help='The folder where the model should be saved.')
parser.add_argument('--clean', action='store_true',
                    help='Does not resume the previous download.')
parser.add_argument('--check', action='store_true',
                    help='Validates the checksums of model files.')
parser.add_argument('--scrap', action='store_true',
                    help='Scraps the repo instead of cloning the files.')
args = parser.parse_args()


def get_file(url, output_folder):
    filename = Path(url.rsplit('/', 1)[1])
    output_path = output_folder / filename
    if output_path.exists() and not args.clean:
        # Check if the file has already been downloaded completely
        r = requests.get(url, stream=True)
        total_size = int(r.headers.get('content-length', 0))
        if output_path.stat().st_size >= total_size:
            return
        # Otherwise, resume the download from where it left off
        headers = {'Range': f'bytes={output_path.stat().st_size}-'}
        mode = 'ab'
    else:
        headers = {}
        mode = 'wb'

    r = requests.get(url, stream=True, headers=headers)
    with open(output_path, mode) as f:
        total_size = int(r.headers.get('content-length', 0))
        block_size = 1024
        with tqdm.tqdm(total=total_size, unit='iB', unit_scale=True, bar_format='{l_bar}{bar}| {n_fmt:6}/{total_fmt:6} {rate_fmt:6}') as t:
            for data in r.iter_content(block_size):
                t.update(len(data))
                f.write(data)


def sanitize_branch_name(branch_name):
    pattern = re.compile(r"^[a-zA-Z0-9._-]+$")
    if pattern.match(branch_name):
        return branch_name
    else:
        raise ValueError(
            "Invalid branch name. Only alphanumeric characters, period, underscore and dash are allowed.")


def select_model_from_default_options():
    models = {
        "OPT 6.7B": ("facebook", "opt-6.7b", "main"),
        "OPT 2.7B": ("facebook", "opt-2.7b", "main"),
        "OPT 1.3B": ("facebook", "opt-1.3b", "main"),
        "OPT 350M": ("facebook", "opt-350m", "main"),
        "GALACTICA 6.7B": ("facebook", "galactica-6.7b", "main"),
        "GALACTICA 1.3B": ("facebook", "galactica-1.3b", "main"),
        "GALACTICA 125M": ("facebook", "galactica-125m", "main"),
        "Pythia-6.9B-deduped": ("EleutherAI", "pythia-6.9b-deduped", "main"),
        "Pythia-2.8B-deduped": ("EleutherAI", "pythia-2.8b-deduped", "main"),
        "Pythia-1.4B-deduped": ("EleutherAI", "pythia-1.4b-deduped", "main"),
        "Pythia-410M-deduped": ("EleutherAI", "pythia-410m-deduped", "main"),
        "Vicuna": ("anon8231489123", "vicuna-13b-GPTQ-4bit-128g", "main"),
    }
    choices = {}

    print("Select the model that you want to download:\n")
    for i, name in enumerate(models):
        char = chr(ord('A')+i)
        choices[char] = name
        print(f"{char}) {name}")
    char = chr(ord('A')+len(models))
    print(f"{char}) None of the above")

    print()
    print("Input> ", end='')
    choice = input()[0].strip().upper()
    if choice == char:
        print("""\nThen type the name of your desired Hugging Face model in the format organization/name.

Examples:
facebook/opt-1.3b
EleutherAI/pythia-1.4b-deduped
""")

        print("Input> ", end='')
        model = input()
        branch = "main"
    else:
        arr = models[choices[choice]]
        model = f"{arr[0]}/{arr[1]}"
        branch = arr[2]

    print("model:", model)
    print("branch:", branch)
    return model, branch


def get_download_links_from_huggingface(model, branch):
    base = "https://huggingface.co"
    # page = f"/{model}/tree/{branch}?cursor="
    # cursor = b""
    page = f"/{model}/tree/{branch}"

    links = []
    sha256 = []
    classifications = []
    has_pytorch = False
    has_pt = False
    has_ggml = False
    has_safetensors = False
    is_lora = False
    while True:
        # content = requests.get(f"{base}{page}{cursor.decode()}").content
        print(f"{base}{page}")
        r = requests.get(f"{base}{page}")
        _content = r.content
        # DEBUG: dump content
        with open("content.html", "wb") as f:
            import os
            print("pwd", os.getcwd())
            f.write(_content)

        # dict = json.loads(content) # BUG: json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
        # dict = json.loads(content.decode())
        # still the same bug
        dict = json.loads(_content.decode('utf-8'))

        if len(dict) == 0:
            break

        for i in range(len(dict)):
            fname = dict[i]['path']
            if not is_lora and fname.endswith(('adapter_config.json', 'adapter_model.bin')):
                is_lora = True

            is_pytorch = re.match("(pytorch|adapter)_model.*\.bin", fname)
            is_safetensors = re.match(".*\.safetensors", fname)
            is_pt = re.match(".*\.pt", fname)
            is_ggml = re.match("ggml.*\.bin", fname)
            is_tokenizer = re.match("tokenizer.*\.model", fname)
            is_text = re.match(".*\.(txt|json|py|md)", fname) or is_tokenizer

            if any((is_pytorch, is_safetensors, is_pt, is_tokenizer, is_text)):
                if 'lfs' in dict[i]:
                    sha256.append([fname, dict[i]['lfs']['oid']])
                if is_text:
                    links.append(
                        f"https://huggingface.co/{model}/resolve/{branch}/{fname}")
                    classifications.append('text')
                    continue
                if not args.text_only:
                    links.append(
                        f"https://huggingface.co/{model}/resolve/{branch}/{fname}")
                    if is_safetensors:
                        has_safetensors = True
                        classifications.append('safetensors')
                    elif is_pytorch:
                        has_pytorch = True
                        classifications.append('pytorch')
                    elif is_pt:
                        has_pt = True
                        classifications.append('pt')
                    elif is_ggml:
                        has_ggml = True
                        classifications.append('ggml')

        cursor = base64.b64encode(
            f'{{"file_name":"{dict[-1]["path"]}"}}'.encode()) + b':50'
        cursor = base64.b64encode(cursor)
        cursor = cursor.replace(b'=', b'%3D')

    # If both pytorch and safetensors are available, download safetensors only
    if (has_pytorch or has_pt) and has_safetensors:
        for i in range(len(classifications)-1, -1, -1):
            if classifications[i] in ['pytorch', 'pt']:
                links.pop(i)

    return links, sha256, is_lora


def download_files(file_list, output_folder, num_threads=8):
    thread_map(lambda url: get_file(url, output_folder),
               file_list, max_workers=num_threads, disable=True)


if __name__ == '__main__':
    model = args.MODEL
    branch = args.branch
    print("scrap:", args.scrap, "\n")

    if model is None:
        model, branch = select_model_from_default_options()
    else:
        if model[-1] == '/':
            model = model[:-1]
            branch = args.branch
        if branch is None:
            branch = "main"
        else:
            try:
                branch = sanitize_branch_name(branch)
            except ValueError as err_branch:
                print(f"Error: {err_branch}")
                sys.exit()

    if args.scrap:
        links, sha256, is_lora = get_download_links_from_huggingface(
            model, branch)
    else:
        links = []
        sha256 = []
        is_lora = False

    if args.output is not None:
        base_folder = args.output
    else:
        base_folder = 'models' if not is_lora else 'loras'

    output_folder = f"{'_'.join(model.split('/')[-2:])}"
    if branch != 'main':
        output_folder += f'_{branch}'
    output_folder = Path(base_folder) / output_folder

    # Downloading the files
    print(f"Downloading the model to {output_folder}")
    if args.scrap:
        download_files(links, output_folder, args.threads)
    else:
        _cmd = f"git clone https://huggingface.co/{model} {output_folder}"
        print(
            f"Cloning the model to {output_folder} using the command: {_cmd}")
        os.system(_cmd)

        # For example: git clone https://huggingface.co/anon8231489123/vicuna-13b-GPTQ-4bit-128g to desired output folder


    # Creating the folder and writing the metadata
    if not output_folder.exists():
        output_folder.mkdir()

    with open(output_folder / 'huggingface-metadata.txt', 'w') as f:
        f.write(f'url: https://huggingface.co/{model}\n')
        f.write(f'branch: {branch}\n')
        f.write(
            f'download date: {str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}\n')
