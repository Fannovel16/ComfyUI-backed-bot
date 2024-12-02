from pathlib import Path
import shutil
import re, ast
from dataclasses import dataclass
import yaml
from PIL import Image

py_workflows_dir = Path(__file__, '..' , 'python_workflows').resolve()
py_workflows_dir.mkdir(exist_ok=True)
preprocessed_dir = Path(__file__, '..', 'preprocessed').resolve()
preprocessed_init_code = """
import sys, os

def find_path(name: str, path: str = None) -> str:
    # If no path is given, use the current working directory
    if path is None:
        path = os.getcwd()

    # Check if the current directory contains the name
    if name in os.listdir(path):
        path_name = os.path.join(path, name)
        print(f"{name} found: {path_name}")
        return path_name

    # Get the parent directory
    parent_directory = os.path.dirname(path)

    # If the parent directory is the same as the current directory, we've reached the root and stop the search
    if parent_directory == path:
        return None

    # Recursively call the function with the parent directory
    return find_path(name, parent_directory)


def add_comfyui_directory_to_sys_path() -> None:
    comfyui_path = find_path("ComfyUI")
    if comfyui_path is not None and os.path.isdir(comfyui_path):
        sys.path.insert(0, comfyui_path)
        print(f"'{comfyui_path}' added to sys.path")


def add_extra_model_paths() -> None:
    try:
        from main import load_extra_path_config
    except ImportError:
        print(
            "Could not import load_extra_path_config from main.py. Looking in utils.extra_config instead."
        )
        from utils.extra_config import load_extra_path_config

    extra_model_paths = find_path("extra_model_paths.yaml")

    if extra_model_paths is not None:
        load_extra_path_config(extra_model_paths)
    else:
        print("Could not find the extra_model_paths config file.")


add_comfyui_directory_to_sys_path()
add_extra_model_paths()


def import_custom_nodes() -> None:
    import asyncio
    import execution
    from nodes import init_extra_nodes
    import server

    # Creating a new event loop and setting it as the default loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Creating an instance of PromptServer with the loop
    server_instance = server.PromptServer(loop)
    execution.PromptQueue(server_instance)

    # Initializing custom nodes
    init_extra_nodes()


from nodes import NODE_CLASS_MAPPINGS
import_custom_nodes()
""".strip()

def preprocess(hooks):
    shutil.rmtree(preprocessed_dir, ignore_errors=True)
    preprocessed_dir.mkdir(exist_ok=True)
    commands = []
    for workflow_py in py_workflows_dir.iterdir():
        if workflow_py.name.startswith('.'): continue #E.g. .ipynb_checkpoints
        if workflow_py.suffix != '.py': continue
        code = workflow_py.read_text(encoding="utf-8")
        start_duplicated, end_duplicated = code.index("def find_path"), code.index("def main")
        code = code[:start_duplicated] + code[end_duplicated:]
        code = code.replace("def main():", f"def main(NODE_CLASS_MAPPINGS, hooks):") \
                    .replace("    import_custom_nodes()", '')
        for hooker in hooks:
            _hooker = hooker.replace('(', '\(').replace(')', '\)')
            code = re.sub(rf"NODE_CLASS_MAPPINGS\[\s*\"{_hooker}\"\s*\]\(\)", f'hooks["{hooker}"]', code)
        temp_file = preprocessed_dir / f"appio_{workflow_py.name}"
        temp_file.write_text(code)
        commands.append(workflow_py.stem)
    
    init_code = preprocessed_init_code + "\n\n" + '\n'.join([f"from .appio_{command} import main as {command}" for command in commands])
    init_file = preprocessed_dir / "__init__.py"
    init_file.write_text(init_code, encoding="utf-8")
    return commands

def extract_execute_arguments(tree, node_id):
    call_node = None
    does_break = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == node_id:
            does_break = True
            continue
        if does_break:
            if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'execute':
                call_node = node
                break
    kwargs = {kw.arg: ast.unparse(kw.value) for kw in call_node.keywords}  # Keyword arguments
    return kwargs

@dataclass
class InputNode:
    class_name: str
    arguments: dict

def get_input_nodes(preprocessed_code):
    appio_nodes = {}
    node_def_pattern = r"(appio_[a-zA-Z0-9]+input.*) = hooks\[(\"AppIO_[a-zA-Z0-9]+\")\]"

    tree = ast.parse(preprocessed_code)
    for node_def_match in re.finditer(node_def_pattern, preprocessed_code):
        node_def_name, node_class = node_def_match.groups()
        node_pattern = fr"(appio_[a-z0-9_]+) = {node_def_name}\.execute"
        for node_id_match in re.finditer(node_pattern, preprocessed_code):
            node_id = node_id_match.group(1)
            arguments = extract_execute_arguments(tree, node_id)
            appio_nodes[node_id] = InputNode(node_class, arguments)
    return appio_nodes

command_input_nodes = None
def analyze_argument_from_preprocessed():
    global command_input_nodes
    if command_input_nodes is not None:
        return command_input_nodes
    else:
        command_input_nodes = {}
    for workflow_py in preprocessed_dir.iterdir():
        if workflow_py.stem.startswith("__"): continue
        command_input_nodes[workflow_py.stem.replace("appio_", '')] = get_input_nodes(workflow_py.read_text(encoding="utf-8"))
    return command_input_nodes

@dataclass
class GuideCommand:
    name: str
    display_name: str
    text: str
    pil_images: list[Image.Image]

class CommandConfig:
    CONFIG_FILE_PATH = Path(py_workflows_dir, "config.yaml")
    CONFIG = (yaml.safe_load(CONFIG_FILE_PATH.read_text(encoding="utf-8"))
            if CONFIG_FILE_PATH.is_file() 
            else {"display_names": {}, "no_return_original": []})
    
    @classmethod
    def get_guide_files(cls):
        return [f for f in py_workflows_dir.iterdir() if f.suffix == '.txt']

    @classmethod
    def get_display_names(cls):
        guides = [f.stem for f in cls.get_guide_files()]
        cmd_names, guide_names = {}, {}
        for command in cls.CONFIG["display_names"]:
            if command in guides or command == "get_user_info": continue
            cmd_names[command] = cls.CONFIG["display_names"].get(command, command)
        for guide in cls.CONFIG["display_names"]:
            if command not in guides or command == "get_user_info": continue
            guide_names[guide] = cls.CONFIG["display_names"].get(guide, guide)
        return cmd_names, guide_names

    @classmethod
    def get_no_return_original(cls):
        return cls.CONFIG["no_return_original"]
    
    @classmethod
    def get_guides(cls):
        guide_files = cls.get_guide_files()
        guides: dict[str, GuideCommand] = {}
        file_exts = ['jpg', 'png', 'jpeg', 'webp']
        _, display_names = cls.get_display_names()
        for guide_file in guide_files:
            guide = guide_file.stem
            image_files = [f for ext in file_exts for f in py_workflows_dir.glob(guide + f"*.{ext}")]
            pil_images = [Image.open(image_file) for image_file in image_files]
            guides[guide] = GuideCommand(guide, display_names.get(guide, guide), guide_file.read_text(encoding="utf-8"), pil_images)
        return guides
        
def serialize_input_nodes(command: str, id: str, prompt: str, input_nodes: list[InputNode]):
    argument_types = {
        "AppIO_StringInput": "String", 
        "AppIO_IntegerInput": "Integer", 
        "AppIO_ImageInput": "Photo", 
        "AppIO_ImageInputFromID": "Photo"
    }
    re = {"String command": command, "String id": id, "String prompt": prompt}
    for input_node in input_nodes:
        arguments = input_node.arguments
        class_name = input_node.class_name.replace('"', '').replace("'", '')
        if "argument_name" in arguments:
            default_value = arguments.get("string", '')
            default_value = default_value or arguments.get("integer", 1)
            
            argument_name = input_node.arguments["argument_name"].replace('"', '').replace("'", '')
            if argument_name == "prompt":
                continue
            argument_type = argument_types.get(class_name, None)
            if (argument_type is None):
                raise NotImplementedError(f"Not found argument type for class {class_name}")
            re[f"{argument_type} {argument_name}"] = default_value
    return '\n'.join(f"{k}: {v}" for k, v in re.items())

def deserialize_input_chain_message(text):
    form = {}
    form_types = {}
    for match in re.finditer(r"([a-zA-Z]+) ([a-zA-Z0-9-_]+): (.+)", text):
        argument_type, argument_name, argument_value = match.groups()
        form[argument_name] = argument_value
        form_types[argument_name] = argument_type
    prompt = None
    prompt_match = re.search(r"([a-zA-Z]+) ([a-zA-Z0-9-_]+)\?", text)
    if prompt_match: prompt = prompt_match.group(2)
    return prompt, form, form_types

if __name__ == "__main__":
    preprocess(["AppIO_StringInput"])