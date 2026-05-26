import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import ansible_runner

from app.models import Host


class RemoteExecutionError(RuntimeError):
    pass


def run_warp_playbook(host: Host, private_key: str, license_key: str, private_data_dir: str) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="warp-runner-", dir=private_data_dir if Path(private_data_dir).exists() else None))
    inventory = root / "inventory"
    inventory.write_text(
        (
            "[warp_targets]\n"
            f"{host.name} ansible_host={host.address} ansible_user={host.ssh_username} "
            f"ansible_port={host.ssh_port} ansible_ssh_private_key_file={root / 'id_key'}\n"
        ),
        encoding="utf-8",
    )
    key_path = root / "id_key"
    key_path.write_text(private_key, encoding="utf-8")
    key_path.chmod(0o600)
    project = root / "project"
    project.mkdir()
    scripts = project / "scripts"
    scripts.mkdir()
    shutil.copyfile(Path(__file__).parents[2] / "scripts" / "warp_native_noninteractive.sh", scripts / "warp_native_noninteractive.sh")
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

    result = ansible_runner.run(
        private_data_dir=str(root),
        playbook=str(playbook),
        inventory=str(inventory),
        extravars={"warp_license_key": license_key},
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
