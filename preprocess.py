from pathlib import Path
import shutil
import re, ast
from dataclasses import dataclass 

py_workflows_dir = Path(__file__, '..' , 'python_workflows').resolve()
py_workflows_dir.mkdir(exist_ok=True)
preprocessed_dir = Path(__file__, '..', 'preprocessed').resolve()

def preprocess(hooks):
    shutil.rmtree(preprocessed_dir, ignore_errors=True)
    preprocessed_dir.mkdir(exist_ok=True)
    commands = []
    for workflow_py in py_workflows_dir.iterdir():
        if workflow_py.name.startswith('.'): continue #E.g. .ipynb_checkpoints
        code = workflow_py.read_text(encoding="utf-8")
        code = code.replace("def main():", f"def main(hooks):") \
                    .replace("sys.path.append(comfyui_path)", "sys.path.insert(0, comfyui_path)") \
                    .replace("    import_custom_nodes()", '') \
                    .replace("from nodes import NODE_CLASS_MAPPINGS", "from nodes import NODE_CLASS_MAPPINGS\nimport_custom_nodes()")
        for hooker in hooks:
            code = code.replace(f'NODE_CLASS_MAPPINGS["{hooker}"]()', f'hooks["{hooker}"]')
        temp_file = preprocessed_dir / f"appio_{workflow_py.name}"
        temp_file.write_text(code)
        commands.append(workflow_py.stem)
    init_file = preprocessed_dir / "__init__.py"
    init_file.write_text('\n'.join([f"from .appio_{command} import main as {command}" for command in commands]), encoding="utf-8")
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

def analyze_argument_from_preprocessed():
    command_input_nodes = {}
    for workflow_py in preprocessed_dir.iterdir():
        if workflow_py.stem.startswith("__"): continue
        command_input_nodes[workflow_py.stem.replace("appio_", '')] = get_input_nodes(workflow_py.read_text(encoding="utf-8"))
    return command_input_nodes

def serialize_input_nodes(command: str, id: str, input_nodes: list[InputNode]):
    argument_types = {
        "AppIO_StringInput": "String", 
        "AppIO_IntegerInput": "Integer", 
        "AppIO_ImageInput": "Photo", 
        "AppIO_ImageInputFromID": "Photo"
    }
    re = {"String command": command, "String id": id}
    for input_node in input_nodes:
        arguments = input_node.arguments
        class_name = input_node.class_name.replace('"', '').replace("'", '')
        if "argument_name" in arguments:
            default_value = arguments.get("string", '')
            default_value = default_value or arguments.get("integer", 1)
            
            argument_name = input_node.arguments["argument_name"].replace('"', '').replace("'", '')
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