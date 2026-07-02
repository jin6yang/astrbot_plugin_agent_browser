export const API_BASE = '/api';

export async function fetchStatus() {
  const res = await fetch(`${API_BASE}/status`);
  if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
  return res.json();
}

export async function fetchVersions(force?: boolean) {
  const url = force ? `${API_BASE}/versions?force=true` : `${API_BASE}/versions`;
  const res = await fetch(url);
  if (!res.ok) {
    let errStr = "Failed to fetch";
    try {
      const data = await res.json();
      if (data.error) errStr = data.error;
    } catch (e) {}
    throw new Error(errStr);
  }
  return res.json();
}

export async function installVersion(version: string, force: boolean, useProxy: boolean) {
  const res = await fetch(`${API_BASE}/install`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version, force, use_proxy: useProxy })
  });
  if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
  return res.json();
}

export async function uninstall() {
  const res = await fetch(`${API_BASE}/uninstall`, { method: 'POST' });
  if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
  return res.json();
}

export async function fetchProgress() {
  const res = await fetch(`${API_BASE}/progress`);
  if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
  return res.json();
}
