from datetime import datetime
import subprocess
import sys
import shutil
import os
from pathlib import Path
import pexpect
from rich.console import Console as RConsole
from rich.table import Table as RTable
import re


from utils import compute_diff, print_diff, parse_table, ExpectedOutput, Diff
import time


prompt_regex = r"\[.*] ?\(.*\) ?> ?$"

# [0.00] (actual_status) > [0.00] (ok) > 
status_line_regex = r"\(([^)]+)\)"

def get_status(status_line: str) -> bool:
    match = re.search(r"\(([^)]+)\)", status_line)
    if match:
        return "ok" in match.group(1)
    else:
        raise Exception("Unexpected status line")

console = RConsole()


"""Parse the .exp file"""
def parse_expected_file(file_nam: Path) -> list[ExpectedOutput]:
    with open(file_nam, "r", encoding="utf-8") as file_name:
        content = file_name.read()
    
    expected = []
    chunks = content.split("*******************\n")
    chunks = chunks[:-1]
    for chunk in chunks:
        lines = chunk.split("\n")
        lines = list(filter(lambda line: len(line) != 0, lines))

        # First line is status and time
        first_line = lines.pop(0)
        status, time = first_line.split()
        time = int(time)

        if len(lines) == 0:
            expected.append(ExpectedOutput(status=="ok", time, None))
        else:
            expected.append(ExpectedOutput(status=="ok", time, parse_table(lines)))

    return expected

        

"""
1. Builds the binary by running Make in the given
submission directory
2. Copies the binary to a temp directory
3. Returns the path to the binary.
"""
def build_binary(submission_dir: Path, entry_nos: list[str]) -> Path:
    output_dir = Path("/tmp/cop290/lab1/")

    output_dir.mkdir(parents=True, exist_ok=True)

    out_binary_name = "spreadsheet"+"_".join(entry_nos)

    try:
        subprocess.run(["make"], cwd=submission_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        binary_path = submission_dir/"target"/"release"/"spreadsheet"
        if not os.path.exists(binary_path):
            raise FileNotFoundError(f"Expected binary '{out_binary_name}' not found in {submission_dir}/target/release")

        shutil.copy2(binary_path, output_dir/out_binary_name)
    except subprocess.CalledProcessError as e:
        print(f"Make failed: {e.stderr.decode()}")
    except FileNotFoundError as e:
        print(f"Error: {e}")

    return output_dir / out_binary_name


"""
Waits for the prompt to appear, and return all the output
before the prompt
"""
def read_till_prompt(child, timeout) -> tuple[str, str]:
    child.expect(prompt_regex, timeout=timeout)
    return child.before, child.after


"""
Runs the command given in the command file.
"""
def run_test(bin_path: Path, cmd_file, exp_out_file):
    # Start the program
    log = open('/tmp/cop290_autograderlog.txt','w')

    child = pexpect.spawn(str(bin_path), args=["999", "18278"], echo=False, encoding='utf-8')
    child.logfile_read = log
    # Expect the spreadsheet that is printed at the start of the program
    read_till_prompt(child, 1)
    
    # Read the command file
    f = open(cmd_file, 'r')
    commands = f.readlines()
    exp_tables = parse_expected_file(exp_out_file)
    assert len(commands) == len(exp_tables), f"Error in test case len(commands)={len(commands)}, len(exp_tables)={len(exp_tables)}"

    # Feed the commands one by one to the program
    start = datetime.now()
    for i, (cmd, exp) in enumerate(zip(commands, exp_tables)):
        if (i+1)%1000==0:
            end = datetime.now()
            print(f"Ran {i} commands in {(end-start).total_seconds()} secs")
            start = datetime.now()

        # Feed command
        child.sendline(cmd)
        time.sleep(0.1)

        # Wait for the prompt to appear and read all the output before it.
        try:
            output, status_line = read_till_prompt(child, timeout=exp.time+0.2)
            # Status line is in the following format:-
            # [0.00] (ok) > [0.00] (ok) > 
            status_line = status_line.lower()
            status_is_ok = get_status(status_line)
        except pexpect.exceptions.TIMEOUT:
            diff = Diff(time_diff=(cmd.strip(), exp.time))
            child.sendline("q")
            print_diff(console, diff, cmd, exp.table, student_table)
            return False

        # Split the output by line.
        output_lines = output.split("\r\n")

        # Remove empty lines
        output_lines: list[str] = list(filter(lambda x: x!="", output_lines))

        # parse into Table
        student_table = parse_table(output_lines)
        # diff with expected table
        diff = compute_diff(exp, student_table, status_is_ok)
        if diff is not None:
            child.sendline("q")
            print_diff(console, diff, cmd, exp.table, student_table)
            return False


    # Quit the program
    child.sendline("q")
    return True


def get_test_case_pairs(test_dir: Path):
    if test_dir.is_file():
        exp_file = test_dir.with_suffix(".exp")  # Change suffix to .exp
        return [(test_dir, exp_file)]

    test_cases = []

    # Find all .cmds files and map them to their corresponding .exp files
    for cmds_file in test_dir.glob("*.cmds"):
        exp_file = cmds_file.with_suffix(".exp")  # Replace .cmds with .exp
        if exp_file.exists():
            test_cases.append((cmds_file, exp_file))

    test_cases.sort()
    return test_cases

if __name__ == "__main__":
    try:
        # /blah/blah/lab1_entry1_entry2_entry_3
        submission_dir = Path(sys.argv[1])
        test_dir = Path(sys.argv[2])

        entry_nos = submission_dir.name.split("_")[1:]
        assert submission_dir.is_dir(), "Must be a directory"
        assert submission_dir.exists(), "Directory must exist"
    except:
        print("Usage: python main.py [submission_dir] [test_dir]")
        exit(1)
    

    bin_path = build_binary(submission_dir, entry_nos)
    test_cases = get_test_case_pairs(test_dir)

    verdict = []
    for (cmd, expected) in test_cases:
        console.print(f"Running {cmd}")
        if not run_test(bin_path, cmd, expected):
            verdict.append((cmd,False))
        else:
            verdict.append((cmd,True))
            

    table = RTable()

    table.add_column("Test Case", justify="right", style="cyan", no_wrap=True)
    table.add_column("Verdict", justify="right", style="cyan", no_wrap=True)
    for (test, is_pass) in verdict:
        if is_pass:
            table.add_row(str(test), "PASS", style="green")
        else:
            table.add_row(str(test), "FAIL", style="red")
    console.print(table)
            
   
        
