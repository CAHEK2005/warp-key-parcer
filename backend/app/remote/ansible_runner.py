import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import ansible_runner

from app.models import Host


class RemoteExecutionError(RuntimeError):
    pass


def run_warp_playbook(
    host: Host,
    license_key: str,
    private_data_dir: str,
    *,
    private_key: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    root, inventory = _prepare_runner_root(host, private_data_dir, private_key=private_key, password=password)
    project = _prepare_project(root)
    playbook = project / "apply-warp.yml"
    playbook.write_text(
        """
- hosts: warp_targets
  become: true
  gather_facts: true
  tasks:
    - name: Upload non-interactive WARP runner
      copy:
        src: scripts/warp_native_noninteractive.sh
        dest: /tmp/warp_native_noninteractive.sh
        mode: "0755"
    - name: Apply WARP license
      command: /tmp/warp_native_noninteractive.sh --license-key {{ warp_license_key | quote }}
      register: warp_result
      changed_when: "'license_applied' in warp_result.stdout"
      failed_when: false
    - name: Return WARP result
      debug:
        msg: "{{ warp_result.stdout_lines[-1] | default('{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"missing output\"}') }}"
""",
        encoding="utf-8",
    )

    return _run_and_extract_json(root, inventory, playbook, extravars={"warp_license_key": license_key})


def run_warp_status(
    host: Host,
    private_data_dir: str,
    *,
    private_key: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    root, inventory = _prepare_runner_root(host, private_data_dir, private_key=private_key, password=password)
    project = _prepare_project(root)
    playbook = project / "warp-status.yml"
    playbook.write_text(
        """
- hosts: warp_targets
  become: true
  gather_facts: true
  tasks:
    - name: Upload non-interactive WARP runner
      copy:
        src: scripts/warp_native_noninteractive.sh
        dest: /tmp/warp_native_noninteractive.sh
        mode: "0755"
    - name: Check WARP account status
      command: /tmp/warp_native_noninteractive.sh --status-only
      register: warp_result
      changed_when: false
      failed_when: false
    - name: Return WARP status
      debug:
        msg: "{{ warp_result.stdout_lines[-1] | default('{\"status\":\"account_status\",\"valid\":false,\"reason\":\"missing output\"}') }}"
""",
        encoding="utf-8",
    )

    return _run_and_extract_json(root, inventory, playbook, extravars={})


def run_warp_restore_latest(
    host: Host,
    private_data_dir: str,
    *,
    private_key: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    root, inventory = _prepare_runner_root(host, private_data_dir, private_key=private_key, password=password)
    project = _prepare_project(root)
    playbook = project / "warp-restore.yml"
    playbook.write_text(
        """
- hosts: warp_targets
  become: true
  gather_facts: true
  tasks:
    - name: Upload non-interactive WARP runner
      copy:
        src: scripts/warp_native_noninteractive.sh
        dest: /tmp/warp_native_noninteractive.sh
        mode: "0755"
    - name: Restore previous WARP account
      command: /tmp/warp_native_noninteractive.sh --restore-latest-backup
      register: warp_result
      changed_when: "'account_restored' in warp_result.stdout"
      failed_when: false
    - name: Return WARP restore status
      debug:
        msg: "{{ warp_result.stdout_lines[-1] | default('{\"status\":\"restore_failed\",\"valid\":false,\"reason\":\"missing output\"}') }}"
""",
        encoding="utf-8",
    )

    return _run_and_extract_json(root, inventory, playbook, extravars={})


def _prepare_runner_root(
    host: Host,
    private_data_dir: str,
    *,
    private_key: str | None,
    password: str | None,
) -> tuple[Path, Path]:
    if not private_key and not password:
        raise RemoteExecutionError("missing ssh credentials")
    base_dir = private_data_dir if Path(private_data_dir).exists() else None
    root = Path(tempfile.mkdtemp(prefix="warp-runner-", dir=base_dir))
    host_vars: dict[str, Any] = {
        "ansible_host": host.address,
        "ansible_user": host.ssh_username,
        "ansible_port": host.ssh_port,
        "ansible_ssh_common_args": "-o StrictHostKeyChecking=no",
    }
    if private_key:
        key_path = root / "id_key"
        key_path.write_text(private_key, encoding="utf-8")
        key_path.chmod(0o600)
        host_vars["ansible_ssh_private_key_file"] = str(key_path)
    if password:
        host_vars["ansible_password"] = password
        host_vars["ansible_become_password"] = password

    inventory = root / "inventory.yml"
    inventory.write_text(json.dumps({"all": {"children": {"warp_targets": {"hosts": {"target": host_vars}}}}}), encoding="utf-8")
    return root, inventory


def _prepare_project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    scripts = project / "scripts"
    scripts.mkdir()
    shutil.copyfile(Path(__file__).parents[2] / "scripts" / "warp_native_noninteractive.sh", scripts / "warp_native_noninteractive.sh")
    return project


def _run_and_extract_json(root: Path, inventory: Path, playbook: Path, extravars: dict[str, Any]) -> dict[str, Any]:
    result = ansible_runner.run(
        private_data_dir=str(root),
        playbook=str(playbook),
        inventory=str(inventory),
        extravars=extravars,
        quiet=True,
    )
    if result.rc != 0:
        return {"status": "ssh_failed", "license_step_reached": False, "reason": f"ansible rc={result.rc}"}
    for event in reversed(list(result.events)):
        data = event.get("event_data", {})
        resolved = data.get("res", {})
        msg = resolved.get("msg")
        if isinstance(msg, str):
            try:
                return json.loads(msg)
            except json.JSONDecodeError:
                continue
    return {"status": "install_failed", "license_step_reached": False, "reason": "no JSON result from playbook"}
