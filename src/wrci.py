import re
import subprocess
import pprint
import sys


class ExecutionStopped(Exception):
    pass


class PipelineParser:
    def __init__(self, dsl_content):
        self.dsl_content = dsl_content
        self.tokens = []
        self.ast = []
        self.variables = {}
        self.current_pipeline = None

    def tokenize(self):
        """Tokenizes the DSL content into meaningful components."""
        lines = self.dsl_content.strip().split("\n")
        for line in lines:
            line = line.strip()
            # Ignore empty lines and comments
            if not line or line.startswith("#"):
                continue
            self.tokens.append(line)

    def parse(self):
        """Parses tokens into an Abstract Syntax Tree (AST) with structured blocks."""
        iterator = iter(self.tokens)
        stack = []
        current_block = self.ast

        for line in iterator:
            if match := re.match(r'PIPELINE\((.*?)\)', line):
                params_str = match.group(1)
                params = dict(re.findall(r'(\w+)="(.*?)"', params_str))

                helper_image = params.get("helper_image")
                start_command = params.get("start_command")
                pipeline_name = params.get("name")

                if helper_image:
                    self.variables["helper_image"] = helper_image
                if start_command:
                    self.variables["start_command"] = start_command
                if pipeline_name:
                    self.variables["pipeline_name"] = pipeline_name

                new_pipeline = {
                    "type": "PIPELINE",
                    "helper_image": helper_image,
                    "start_command": start_command,
                    "name": pipeline_name,
                    "body": []
                }

                current_block.append(new_pipeline)
                stack.append(current_block)
                current_block = new_pipeline["body"]

            elif match := re.match(r'STEP (.+)', line):
                current_block.append({"type": "STEP", "script": match.group(1)})

            elif match := re.match(r'IF \$(\w+)\s*([=!]=)\s*"(.+?)":', line):
                new_if_block = {
                    "type": "IF",
                    "variable": match.group(1),
                    "operator": match.group(2),
                    "value": match.group(3),
                    "body": []
                }
                current_block.append(new_if_block)
                stack.append(current_block)
                current_block = new_if_block["body"]

            elif match := re.match(r'ELSE:', line):
                parent_block = stack.pop()
                else_block = {"type": "ELSE", "body": []}
                parent_block[-1]["else"] = else_block
                stack.append(parent_block)
                current_block = else_block["body"]

            elif match := re.match(r'MSG\("(.+?)"\)', line):
                current_block.append({"type": "MSG", "message": match.group(1)})

            elif match := re.match(r'\$(\w+)\s*=\s*"(.+?)"', line):
                current_block.append({
                    "type": "ASSIGN",
                    "name": match.group(1),
                    "value": match.group(2)
                })

            elif match := re.match(r'END', line):
                if stack:
                    # Pop back to previous block
                    current_block = stack.pop()
                else:
                    raise SyntaxError("END without matching block")

            elif match := re.match(r'EXIT', line):
                current_block.append({ "type": "EXIT" })

    def get_ast(self):
        """Returns the parsed AST."""
        return {"variables": self.variables, "ast": self.ast}


class PipelineExecutor:
    def __init__(self, ast):
        self.ast = ast["ast"]
        self.variables = ast["variables"]
        self.last_rc = 0
        self.running_containers = {}

    def start_container(self, pipeline, parent_container_id=None):
        pipeline_name = pipeline.get("name")
        helper_image = pipeline.get("helper_image")

        if not pipeline_name:
            raise ValueError("Pipeline must have a 'name' to assign a container")

        if pipeline_name in self.running_containers:
            print(f"Using existing container for pipeline '{pipeline_name}'")
            return self.running_containers[pipeline_name]

        if not helper_image:
            if parent_container_id:
                print(f"Reusing parent container for pipeline '{pipeline_name}'")
                return parent_container_id
            else:
                raise ValueError(f"Cannot start pipeline '{pipeline_name}': no helper_image and no parent container")

        start_command = pipeline.get("start_command")
        print(f"Starting container '{pipeline_name}' with image: {helper_image}")

        mount_path = "/home/wreiner/tmp/_sem6-swd22/MDD/wrci/.wrci:/pipeline"
        command = [
            "docker", "run", "-d", "--rm",
            "--name", pipeline_name,
            "-v", mount_path,
            "-v", "/home/wreiner/tmp/_sem6-swd22/MDD/wrci/testpipeline-src:/src",
            helper_image
        ]
        if start_command:
            command.extend(["/bin/sh", "-c", start_command])

        result = subprocess.run(command, capture_output=True, text=True)
        container_id = result.stdout.strip()
        print(f"Container '{pipeline_name}' started with ID: {container_id}")

        self.running_containers[pipeline_name] = container_id
        return container_id

    def ostart_container(self, pipeline):
        """Starts a persistent container for the pipeline if not already running."""
        pipeline_name = pipeline.get("name")
        if not pipeline_name:
            raise ValueError("Pipeline must have a 'name' to assign a container")

        if pipeline_name in self.running_containers:
            print(f"Using existing container for pipeline '{pipeline_name}'")
            return self.running_containers[pipeline_name]

        image = pipeline.get("helper_image")
        start_command = pipeline.get("start_command")
        print(f"Starting container '{pipeline_name}' with image: {image}")

        mount_path = "/home/wreiner/tmp/_sem6-swd22/MDD/wrci/.wrci:/pipeline"
        command = [
            "docker", "run", "-d", "--rm",
            "--name", pipeline_name,  # 🔥 name container explicitly
            "-v", mount_path,
            "-v", "/home/wreiner/tmp/_sem6-swd22/MDD/wrci/testpipeline-src:/src",
            image
        ]
        if start_command:
            command.extend(["/bin/sh", "-c", start_command])

        result = subprocess.run(command, capture_output=True, text=True)
        container_id = result.stdout.strip()
        print(f"Container '{pipeline_name}' started with ID: {container_id}")

        self.running_containers[pipeline_name] = container_id
        return container_id

    def stop_all_containers(self):
        """Stops all running containers when execution completes."""
        for pipeline_name, container_id in self.running_containers.items():
            print(f"Stopping container {container_id} for pipeline {pipeline_name}")
            # subprocess.run(["docker", "stop", container_id])
            subprocess.run(["docker", "kill", container_id])
        self.running_containers.clear()

    def run_step(self, script, pipeline_name, container_id):
        """Executes a step inside the assigned container with all current variables as environment variables."""
        script_path = f"/pipeline/{pipeline_name}/{script}" if pipeline_name else f"/pipeline/{script}"

        # Prepare environment variables
        env_args = []
        for key, value in self.variables.items():
            env_args.extend(["-e", f"{key}={value}"])

        print(f"Executing step: {script_path} in container {container_id}")
        result = subprocess.run(
            ["docker", "exec"] + env_args + [container_id, "/bin/sh", "-c", script_path],
            capture_output=True,
            text=True
        )

        print(result.stdout)
        print(result.stderr)
        self.last_rc = result.returncode

    def run_pipeline(self, pipeline, parent_container_id=None):
        container_id = self.start_container(pipeline, parent_container_id)
        self.execute_block(pipeline["body"], container_id)
        self.stop_all_containers()

    def execute(self):
        """Executes the parsed AST, ensuring proper pipeline and conditional execution."""
        try:
            for node in self.ast:
                if node["type"] == "PIPELINE":
                    container_id = self.run_pipeline(node, parent_container_id=None)
                    self.execute_block(node["body"], container_id, node["name"])
        except ExecutionStopped:
            print("Execution exited early due to EXIT command.")
        finally:
            self.stop_all_containers()


    def execute_block(self, block, container_id, pipeline_name=None):
        """Executes a block of statements, ensuring IF/ELSE logic executes correctly."""
        skip_else = False

        for node in block:
            if node["type"] == "PIPELINE":
                container_id = self.run_pipeline(node, parent_container_id=container_id)
                self.execute_block(node["body"], container_id, node["name"])

            elif node["type"] == "MSG":
                print(f"Message: {node['message']}")

            elif node["type"] == "STEP":
                self.run_step(node["script"], pipeline_name, container_id)

            elif node["type"] == "ASSIGN":
                self.variables[node["name"]] = node["value"]

            elif node["type"] == "IF":
                var_name = node["variable"]
                operator = node.get("operator", "==")  # default to '=='
                expected = node["value"]
                actual = self.variables.get(var_name)

                if operator == "==" and str(actual) == str(expected):
                    self.execute_block(node["body"], container_id, pipeline_name)

                elif operator == "!=" and str(actual) != str(expected):
                    self.execute_block(node["body"], container_id, pipeline_name)

                elif "else" in node:
                    self.execute_block(node["else"]["body"], container_id, pipeline_name)

            elif node["type"] == "ELSE" and not skip_else:
                self.execute_block(node["body"], container_id, pipeline_name)

            elif node["type"] == "END":
                        return

            elif node["type"] == "EXIT":
                raise ExecutionStopped()


dsl_script = """
PIPELINE(helper_image="debian:bookworm-slim", start_command="sleep infinity", name="compile-verify")

    MSG("Outer pipeline")

    $wrciarch = "armv7"

    PIPELINE(name="deploy")
        MSG("Inner pipeline")
        EXIT
        MSG("End of Inner pipeline")
    END

    # PIPELINE(helper_image="debian:bookworm-slim", start_command="sleep infinity", name="deploy")
    #     MSG("Inner pipeline")
    #     EXIT
    #     MSG("End of Inner pipeline")
    # END

    STEP step-envvar.sh

    MSG("End of outer pipeline")
END
"""

parser = PipelineParser(dsl_script)
parser.tokenize()
parser.parse()
parsed_ast = parser.get_ast()

pprint.pprint(parsed_ast)

executor = PipelineExecutor(parsed_ast)
executor.execute()
