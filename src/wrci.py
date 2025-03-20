import re
import subprocess
import pprint


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
            if not line or line.startswith("#"):  # Ignore empty lines and comments
                continue
            self.tokens.append(line)

    def parse(self):
        """Parses tokens into an Abstract Syntax Tree (AST) with structured blocks."""
        iterator = iter(self.tokens)
        stack = []  # Stack to keep track of nested blocks
        current_block = self.ast

        for line in iterator:
            if match := re.match(r'PIPELINE\(helper_image="(.+?)"(?:, start_command="(.+?)")?(?:, name="(.+?)")?\)', line):
                self.variables["helper_image"] = match.group(1)
                start_command = match.group(2) if match.group(2) else None
                name = match.group(3) if match.group(3) else None
                new_pipeline = {"type": "PIPELINE", "helper_image": match.group(1), "start_command": start_command, "name": name, "body": []}
                current_block.append(new_pipeline)
                stack.append(current_block)
                current_block = new_pipeline["body"]
            elif match := re.match(r'STEP (.+)', line):
                current_block.append({"type": "STEP", "script": match.group(1)})
            elif match := re.match(r'IF \$(\w+) == (\d+):', line):
                new_if_block = {"type": "IF", "variable": match.group(1), "value": int(match.group(2)), "body": []}
                current_block.append(new_if_block)
                stack.append(current_block)
                current_block = new_if_block["body"]
            elif match := re.match(r'ELSE:', line):
                if stack:
                    last_block = stack.pop()  # Go back to the parent block
                    new_else_block = {"type": "ELSE", "body": []}
                    last_block[-1]["else"] = new_else_block  # Attach ELSE to last IF
                    stack.append(current_block)
                    current_block = new_else_block["body"]
            elif match := re.match(r'MSG\("(.+?)"\)', line):
                current_block.append({"type": "MSG", "message": match.group(1)})
            elif match := re.match(r'END', line):
                if stack:
                    current_block = stack.pop()  # Return to previous block

    def o1parse(self):
        """Parses tokens into an Abstract Syntax Tree (AST) with structured blocks."""
        iterator = iter(self.tokens)
        stack = []  # Stack to keep track of nested IF/ELSE blocks
        current_block = self.ast

        for line in iterator:
            if match := re.match(r'PIPELINE\(helper_image="(.+?)"(?:, start_command="(.+?)")?(?:, name="(.+?)")?\)', line):
                self.variables["helper_image"] = match.group(1)
                start_command = match.group(2) if match.group(2) else None
                name = match.group(3) if match.group(3) else None
                new_pipeline = {"type": "PIPELINE", "helper_image": match.group(1), "start_command": start_command, "name": name, "body": []}
                current_block.append(new_pipeline)
                stack.append((current_block, new_pipeline))
                current_block = new_pipeline["body"]
            elif match := re.match(r'STEP (.+)', line):
                current_block.append({"type": "STEP", "script": match.group(1)})
            elif match := re.match(r'IF \$(\w+) == (\d+):', line):
                new_if_block = {"type": "IF", "variable": match.group(1), "value": int(match.group(2)), "then": []}
                current_block.append(new_if_block)
                stack.append((current_block, new_if_block, "then"))
                current_block = new_if_block["then"]
            elif match := re.match(r'ELSE:', line):
                if stack:
                    _, last_if_block, _ = stack[-1]
                    last_if_block["else"] = []
                    current_block = last_if_block["else"]
            elif match := re.match(r'MSG\("(.+?)"\)', line):
                current_block.append({"type": "MSG", "message": match.group(1)})
            elif match := re.match(r'END', line):
                if stack:
                    current_block, _ = stack.pop()[:2]

    def get_ast(self):
        """Returns the parsed AST."""
        return {"variables": self.variables, "ast": self.ast}


class PipelineExecutor:
    def __init__(self, ast):
        self.ast = ast["ast"]
        self.variables = ast["variables"]
        self.last_rc = 0
        self.running_containers = {}

    def start_container(self, pipeline):
        """Starts a persistent container for the pipeline if not already running."""
        pipeline_name = pipeline.get("name")
        if pipeline_name in self.running_containers:
            print(f"Using existing container for pipeline {pipeline_name}")
            return self.running_containers[pipeline_name]

        image = pipeline["helper_image"]
        start_command = pipeline.get("start_command")
        print(f"Starting container for pipeline {pipeline_name} with image: {image}")
        mount_path = "/home/wreiner/tmp/_sem6-swd22/MDD/wrci/.wrci:/pipeline"
        command = ["docker", "run", "-d", "--rm", "-v", mount_path, "-v", "/home/wreiner/tmp/_sem6-swd22/MDD/wrci/testpipeline-src:/src", image]
        if start_command:
            command.extend(["/bin/sh", "-c", start_command])
        result = subprocess.run(command, capture_output=True, text=True)
        container_id = result.stdout.strip()
        print(f"Container started with ID: {container_id}")
        self.running_containers[pipeline_name] = container_id
        return container_id

    def stop_all_containers(self):
        """Stops all running containers when execution completes."""
        for pipeline_name, container_id in self.running_containers.items():
            print(f"Stopping container {container_id} for pipeline {pipeline_name}")
            subprocess.run(["docker", "stop", container_id])
        self.running_containers.clear()

    def run_step(self, script, pipeline_name, container_id):
        """Executes a step inside the assigned container."""
        script_path = f"/pipeline/{pipeline_name}/{script}" if pipeline_name else f"/pipeline/{script}"
        print(f"Executing step: {script_path} in container {container_id}")
        result = subprocess.run([
            "docker", "exec", container_id, "/bin/sh", "-c", script_path
        ], capture_output=True, text=True)
        print(result.stdout)
        print(result.stderr)
        self.last_rc = result.returncode

    def run_pipeline(self, pipeline):
        container_id = self.start_container(pipeline)
        self.execute_block(pipeline["body"], container_id)
        self.stop_all_containers()

    def execute(self):
        """Executes the parsed AST, ensuring proper pipeline and conditional execution."""
        for node in self.ast:
            if node["type"] == "PIPELINE":
                container_id = self.start_container(node)
                self.execute_block(node["body"], container_id, node["name"])
        self.stop_all_containers()

    def execute_block(self, block, container_id, pipeline_name=None):
        """Executes a block of statements, ensuring IF/ELSE logic executes correctly."""
        skip_else = False

        for node in block:
            if node["type"] == "PIPELINE":
                if node["name"] in self.running_containers:
                    container_id = self.running_containers[node["name"]]
                else:
                    container_id = self.start_container(node)
                self.execute_block(node["body"], container_id, node["name"])
            elif node["type"] == "MSG":
                print(f"Message: {node['message']}")
            elif node["type"] == "STEP":
                self.run_step(node["script"], pipeline_name, container_id)
            elif node["type"] == "IF":
                condition_var = node["variable"]
                condition_value = node["value"]
                var_value = self.last_rc

                if var_value == condition_value:
                    self.execute_block(node["body"], container_id, pipeline_name)
                    skip_else = True
            elif node["type"] == "ELSE" and not skip_else:
                self.execute_block(node["body"], container_id, pipeline_name)


    def o2execute_block(self, block, container_id, pipeline_name=None):
        """Executes a block of statements, handling PIPELINE, IF, ELSE, and other tasks."""
        for node in block:
            if node["type"] == "PIPELINE":
                if node["name"] in self.running_containers:
                    container_id = self.running_containers[node["name"]]
                else:
                    container_id = self.start_container(node)
                self.execute_block(node["body"], container_id, node["name"])
            elif node["type"] == "MSG":
                print(f"Message: {node['message']}")
            elif node["type"] == "STEP":
                self.run_step(node["script"], pipeline_name, container_id)
            elif node["type"] == "IF":
                if self.last_rc == node["value"]:
                    self.execute_block(node["body"], container_id, pipeline_name)
                elif "else" in node:
                    self.execute_block(node["else"]["body"], container_id, pipeline_name)
            elif node["type"] == "ELSE":
                self.execute_block(node["body"], container_id, pipeline_name)


    def o1execute_block(self, block, container_id):
        for node in block:
            if node["type"] == "MSG":
                print(f"Message: {node['message']}")
            elif node["type"] == "STEP":
                self.run_step(node["script"], None, container_id)
            elif node["type"] == "IF":
                if self.last_rc == node["value"]:
                    self.execute_block(node["then"], container_id)
                elif "else" in node:
                    self.execute_block(node["else"], container_id)

    def o1execute(self):
        for node in self.ast:
            if node["type"] == "PIPELINE":
                self.run_pipeline(node)
        self.stop_all_containers()


dsl_script = """
PIPELINE(helper_image="debian:bookworm-slim", start_command="sleep infinity", name="compile-verify")

MSG("Starting steps")

# STEP step-prepare.sh
STEP step-patch.sh
# STEP step-compile.sh
# STEP step-validate.sh

IF $LAST_RC == 0:
    MSG("New version deployed")
    STEP step-fail.sh
    IF $LAST_RC == 1:
        MSG("This should not happen")
ELSE:
    MSG("Validation failed")
    IF $LAST_RC == 2:
        MSG("Critical failure")
    END
"""

parser = PipelineParser(dsl_script)
parser.tokenize()
parser.parse()
parsed_ast = parser.get_ast()

pprint.pprint(parsed_ast)

executor = PipelineExecutor(parsed_ast)
executor.execute()
