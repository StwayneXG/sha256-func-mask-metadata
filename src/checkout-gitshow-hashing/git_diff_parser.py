import subprocess

class GitDiffParser:
    @staticmethod
    def get_diff(project_dir):
        cmd = "git show --no-prefix -U500"
        result = subprocess.run(cmd, cwd=project_dir, shell=True, capture_output=True, text=True, check=True, errors='ignore')
        return result.stdout
