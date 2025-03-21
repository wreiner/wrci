import argparse
import os
import pprint
import re
import subprocess


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
    def __init__(self, ast, volumes=None):
        self.ast = ast["ast"]
        self.variables = ast["variables"]
        self.last_rc = 0
        self.running_containers = {}
        self.volumes = volumes or []

    def start_container(self, pipeline, parent_container_id=None):
        pipeline_name = pipeline.get("name")
        helper_image = pipeline.get("helper_image")
        start_command = pipeline.get("start_command")

        if not pipeline_name:
            raise ValueError("Pipeline must have a 'name' to assign a container")

        if pipeline_name in self.running_containers:
            print(f"Using existing container for pipeline '{pipeline_name}'")
            return self.running_containers[pipeline_name]

        if not helper_image:
            if parent_container_id:
                print(f"Pipeline '{pipeline_name}' has no helper_image. Reusing parent container.")
                return parent_container_id
            else:
                raise ValueError(f"Cannot start pipeline '{pipeline_name}': no helper_image and no parent container.")

        print(f"Starting container '{pipeline_name}' with image: {helper_image}")

        command = [
            "docker", "run", "-d", "--rm",
            "--name", pipeline_name
        ]

        # Add all -v volumes
        for volume in self.volumes:
            host, container = volume.split(":", 1)
            if not os.path.isabs(host):
                host = os.path.abspath(os.path.join(os.getcwd(), host))
            command.extend(["-v", f"{host}:{container}"])

        command.append(helper_image)

        if start_command:
            command.extend(["/bin/sh", "-c", start_command])

        print(f"Running command: {' '.join(command)}")

        result = subprocess.run(command, capture_output=True, text=True)
        container_id = result.stdout.strip()

        if result.returncode != 0:
            print(result.stderr)
            raise RuntimeError(f"Failed to start container for pipeline '{pipeline_name}'")

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
        self.variables["LAST_RC"] = str(self.last_rc)

    def run_pipeline(self, pipeline, parent_container_id=None):
        pipeline_name = pipeline.get("name")

        if pipeline.get("helper_image"):
            container_id = self.start_container(pipeline, parent_container_id)
        else:
            container_id = parent_container_id
            print(f"Using parent container for pipeline '{pipeline_name}'")

        self.execute_block(pipeline["body"], container_id, pipeline_name)

        # Return the container used so the parent can continue with it
        return parent_container_id if pipeline.get("helper_image") else container_id

    def execute(self):
        try:
            if len(self.ast) != 1 or self.ast[0]["type"] != "PIPELINE":
                raise ValueError("Top-level AST must contain exactly one PIPELINE node")
            self.run_pipeline(self.ast[0])
        except ExecutionStopped:
            print("Execution exited early due to EXIT command.")
        finally:
            self.stop_all_containers()

    def execute_block(self, block, container_id, pipeline_name=None):
        """Executes a block of statements, ensuring IF/ELSE logic executes correctly."""
        skip_else = False

        for node in block:
            if node["type"] == "PIPELINE":
                # Save the original container before entering nested pipeline
                current_container_id = container_id
                # Run nested pipeline, possibly creating a new container
                returned_container_id = self.run_pipeline(node, parent_container_id=container_id)
                # Restore original container for outer block
                container_id = current_container_id

            elif node["type"] == "MSG":
                msg = node["message"]
                for var_name, var_value in self.variables.items():
                    msg = msg.replace(f"${var_name}", str(var_value))
                print(f"Message: {msg}")

            elif node["type"] == "STEP":
                self.run_step(node["script"], pipeline_name, container_id)

            elif node["type"] == "ASSIGN":
                self.variables[node["name"]] = node["value"]

            elif node["type"] == "IF":
                var_name = node["variable"]
                operator = node.get("operator", "==")
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


if __name__ == "__main__":
    parser_cli = argparse.ArgumentParser(description="Run WRCI pipeline")
    parser_cli.add_argument("--pipelinefile", required=True, help="Path to the pipeline file to run")
    parser_cli.add_argument("-v", "--volume", action="append", help="Bind mount a volume (host:container)", default=[])
    args = parser_cli.parse_args()

    with open(args.pipelinefile, "r") as f:
        dsl_script = f.read()

        parser = PipelineParser(dsl_script)
        parser.tokenize()
        parser.parse()
        parsed_ast = parser.get_ast()

        print("Parsed AST:")
        pprint.pprint(parsed_ast)
        print("End of AST\n")

        print("Executing pipeline...")
        executor = PipelineExecutor(parsed_ast, volumes=args.volume)
        executor.execute()
