import subprocess
import os

class ProjectCheckout:
    def __init__(self, base_dir):
        self.base_dir = base_dir

    def checkout(self, project, bug_num):
        output_dir = os.path.join(self.base_dir, f"{project}_{bug_num}")
        cmd = f"defects4j checkout -p {project} -v {bug_num}b -w {output_dir}"
        subprocess.run(cmd, shell=True, check=True)
        return output_dir