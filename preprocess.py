from pathlib import Path
import shutil
py_workflows_dir = Path(__file__, '..' , 'python_workflows')
py_workflows_dir.mkdir(exist_ok=True)
preprocessed_dir = Path(__file__, '..', 'preprocessed')

def preprocess(hooks):
    shutil.rmtree(preprocessed_dir, ignore_errors=True)
    preprocessed_dir.mkdir()
    commands = []
    for workflow_py in py_workflows_dir.iterdir():
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
    init_file.write_text('\n'.join([f"from .appio_{command} import main as {command}" for command in commands]))
    return commands

if __name__ == "__main__":
    preprocess(["AppIO_StringInput"])