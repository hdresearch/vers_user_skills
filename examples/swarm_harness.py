#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["rich"]
# ///

"""
Swarm Harness — Quality Coordination for Verse Lieutenants

Features:
- Task queue management
- Parallel execution with dependency tracking
- Output verification
- State checkpointing
- Rich terminal UI

Usage:
    # Interactive dashboard
    uv run examples/swarm_harness.py dashboard

    # Execute workflow from config
    uv run examples/swarm_harness.py run workflow.json

    # Checkpoint all lieutenants
    uv run examples/swarm_harness.py checkpoint

Author: Carter Schonwald
Date: 2026-04-03
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, Progress, TaskID, TextColumn
from rich.table import Table

console = Console()

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

STATE_PATH = Path.home() / ".vers/lieutenants.json"
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
CHECKPOINT_DIR = Path.home() / ".vers/checkpoints"


# ──────────────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Task:
    """Single task in workflow"""
    id: str
    lieutenant: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    status: str = "pending"  # pending, running, complete, failed
    started_at: str | None = None
    completed_at: str | None = None
    output_marker: str | None = None  # string to search for in output


@dataclass
class Workflow:
    """Collection of coordinated tasks"""
    name: str
    tasks: list[Task]
    metadata: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# Lieutenant Interface
# ──────────────────────────────────────────────────────────────────────────────

def load_lieutenant_state() -> dict:
    """Load lieutenant state from disk"""
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())["lieutenants"]
    return {}


def send_task(lieutenant: str, message: str, mode: str = "prompt") -> bool:
    """Send task to lieutenant via lt.py"""
    try:
        subprocess.run(
            ["uv", "run", str(SCRIPTS_DIR / "lt.py"), "lt-send", lieutenant, message, "--mode", mode],
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to send task to {lieutenant}:[/] {e.stderr}")
        return False


def read_output(lieutenant: str, tail: int = 50) -> str:
    """Read recent output from lieutenant"""
    try:
        result = subprocess.run(
            ["uv", "run", str(SCRIPTS_DIR / "lt.py"), "lt-read", lieutenant, "--tail", str(tail)],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


def get_lieutenant_status(lieutenant: str) -> dict:
    """Get lieutenant status from state file"""
    lts = load_lieutenant_state()
    return lts.get(lieutenant, {})


# ──────────────────────────────────────────────────────────────────────────────
# Workflow Execution
# ──────────────────────────────────────────────────────────────────────────────

def can_run_task(task: Task, completed: set[str]) -> bool:
    """Check if task's dependencies are satisfied"""
    return all(dep in completed for dep in task.dependencies)


def check_task_completion(task: Task) -> bool:
    """Check if task has completed (via output marker or status)"""
    if task.output_marker:
        output = read_output(task.lieutenant, tail=100)
        if task.output_marker in output:
            return True
    
    # Fallback: check lieutenant status
    status = get_lieutenant_status(task.lieutenant)
    return status.get("status") == "idle"


def execute_workflow(workflow: Workflow, poll_interval: int = 10) -> bool:
    """Execute workflow with dependency management"""
    
    console.print(f"[bold cyan]Starting workflow: {workflow.name}[/]")
    
    pending = {t.id: t for t in workflow.tasks if t.status == "pending"}
    running: dict[str, Task] = {}
    completed: set[str] = set()
    failed: set[str] = set()
    
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
    )
    
    with Live(progress, console=console, refresh_per_second=1):
        # Create progress bars
        task_bars: dict[str, TaskID] = {}
        for task in workflow.tasks:
            task_bars[task.id] = progress.add_task(
                f"[cyan]{task.lieutenant}:[/] {task.description[:50]}...",
                total=100,
            )
        
        while pending or running:
            # Start tasks whose dependencies are met
            for task_id, task in list(pending.items()):
                if can_run_task(task, completed):
                    console.print(f"[green]Starting:[/] {task.id} on {task.lieutenant}")
                    
                    if send_task(task.lieutenant, task.description):
                        task.status = "running"
                        task.started_at = datetime.now().isoformat()
                        running[task_id] = task
                        del pending[task_id]
                        progress.update(task_bars[task_id], completed=33)
                    else:
                        task.status = "failed"
                        failed.add(task_id)
                        del pending[task_id]
                        progress.update(task_bars[task_id], completed=0, description=f"[red]FAILED: {task.description[:50]}...")
            
            # Check running tasks for completion
            for task_id, task in list(running.items()):
                if check_task_completion(task):
                    console.print(f"[green]✓[/] {task.id} completed")
                    task.status = "complete"
                    task.completed_at = datetime.now().isoformat()
                    completed.add(task_id)
                    del running[task_id]
                    progress.update(task_bars[task_id], completed=100)
                else:
                    progress.update(task_bars[task_id], completed=66)
            
            # No more tasks can start and nothing is running — deadlock or done
            if not running and pending:
                console.print("[red]Deadlock detected — unmet dependencies:[/]")
                for task_id, task in pending.items():
                    unmet = [d for d in task.dependencies if d not in completed]
                    console.print(f"  {task_id}: waiting on {unmet}")
                return False
            
            time.sleep(poll_interval)
    
    success = len(completed) == len(workflow.tasks)
    if success:
        console.print("[bold green]✓ Workflow completed successfully[/]")
    else:
        console.print(f"[bold red]✗ Workflow failed: {len(failed)} tasks failed, {len(pending)} pending[/]")
    
    return success


# ──────────────────────────────────────────────────────────────────────────────
# Workflow Loading
# ──────────────────────────────────────────────────────────────────────────────

def load_workflow(path: Path) -> Workflow:
    """Load workflow from JSON config"""
    data = json.loads(path.read_text())
    tasks = [
        Task(
            id=t["id"],
            lieutenant=t["lieutenant"],
            description=t["description"],
            dependencies=t.get("dependencies", []),
            output_marker=t.get("output_marker"),
        )
        for t in data["tasks"]
    ]
    return Workflow(
        name=data["name"],
        tasks=tasks,
        metadata=data.get("metadata", {}),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────────────────────

def show_dashboard():
    """Display interactive dashboard"""
    lts = load_lieutenant_state()
    
    if not lts:
        console.print("[yellow]No lieutenants found[/]")
        console.print("Create lieutenants with: uv run scripts/lt.py lt-create <name> <role> <commit-id>")
        return
    
    table = Table(title="Swarm Dashboard", show_header=True, header_style="bold magenta")
    table.add_column("Lieutenant", style="cyan", width=15)
    table.add_column("Status", style="white", width=10)
    table.add_column("Role", style="dim", width=40)
    table.add_column("Tasks", justify="right", style="green", width=6)
    table.add_column("Last Activity", style="yellow", width=20)
    
    status_icons = {
        "working": "⟳",
        "idle": "●",
        "paused": "⏸",
        "error": "✗",
    }
    
    for name, lt in sorted(lts.items()):
        icon = status_icons.get(lt.get("status", "?"), "○")
        status = f"{icon} {lt.get('status', '?')}"
        
        table.add_row(
            name,
            status,
            lt.get("role", "")[:40],
            str(lt.get("taskCount", 0)),
            lt.get("lastActivityAt", "N/A")[:19].replace("T", " "),
        )
    
    console.print(table)
    console.print()
    console.print("[dim]Commands:[/]")
    console.print("  uv run scripts/lt.py lt-send <name> '<task>'")
    console.print("  uv run scripts/lt.py lt-read <name> --follow")
    console.print("  uv run scripts/lt.py lt-status --probe")


# ──────────────────────────────────────────────────────────────────────────────
# Checkpointing
# ──────────────────────────────────────────────────────────────────────────────

def checkpoint_swarm():
    """Snapshot all active lieutenants"""
    lts = load_lieutenant_state()
    
    if not lts:
        console.print("[yellow]No lieutenants to checkpoint[/]")
        return
    
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    checkpoint_dir = CHECKPOINT_DIR / timestamp
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[cyan]Creating checkpoint at {checkpoint_dir}[/]")
    
    for name, lt in lts.items():
        vm_id = lt["vmId"]
        console.print(f"  Snapshotting {name} (vm={vm_id[:12]})...")
        
        try:
            result = subprocess.run(
                ["uv", "run", str(SCRIPTS_DIR / "vers_api.py"), "vm-commit", vm_id],
                capture_output=True,
                text=True,
                check=True,
            )
            commit_data = json.loads(result.stdout)
            
            # Save metadata
            metadata = {
                "lieutenant": name,
                "vm_id": vm_id,
                "commit": commit_data,
                "state": lt,
                "timestamp": timestamp,
            }
            (checkpoint_dir / f"{name}.json").write_text(json.dumps(metadata, indent=2))
            
            console.print(f"    [green]✓[/] commit: {commit_data.get('id', '?')[:16]}...")
        
        except subprocess.CalledProcessError as e:
            console.print(f"    [red]✗[/] failed: {e.stderr}")
    
    # Save overall state
    (checkpoint_dir / "swarm_state.json").write_text(json.dumps({"lieutenants": lts}, indent=2))
    console.print(f"[green]Checkpoint complete:[/] {checkpoint_dir}")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Swarm Harness — Quality coordination for Verse lieutenants",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    
    sub.add_parser("dashboard", help="Show swarm dashboard")
    
    p = sub.add_parser("run", help="Execute workflow from config")
    p.add_argument("config", type=Path, help="workflow JSON file")
    p.add_argument("--poll-interval", type=int, default=10, help="seconds between checks")
    
    sub.add_parser("checkpoint", help="Snapshot all lieutenants")
    
    args = parser.parse_args()
    
    if args.cmd == "dashboard":
        show_dashboard()
    
    elif args.cmd == "run":
        if not args.config.exists():
            console.print(f"[red]Config not found:[/] {args.config}")
            return 1
        
        workflow = load_workflow(args.config)
        success = execute_workflow(workflow, poll_interval=args.poll_interval)
        return 0 if success else 1
    
    elif args.cmd == "checkpoint":
        checkpoint_swarm()
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
