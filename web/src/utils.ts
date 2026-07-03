export function formatSpeed(bps: number | null): string {
  if (!bps) return '—';
  if (bps >= 1000000000) return `${(bps / 1000000000).toFixed(0)} Gbps`;
  if (bps >= 1000000) return `${(bps / 1000000).toFixed(0)} Mbps`;
  return `${bps} bps`;
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let val = bytes;
  let unitIdx = 0;
  while (val >= 1024 && unitIdx < units.length - 1) {
    val /= 1024;
    unitIdx++;
  }
  return `${val.toFixed(1)} ${units[unitIdx]}`;
}

export function formatIsoDate(iso: string | null | undefined, style: 'datetime' | 'time' | 'date'): string {
  if (!iso) return '—';
  const d = new Date(iso + 'Z');
  if (style === 'time') return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (style === 'date') return d.toLocaleDateString();
  return d.toLocaleString();
}

export interface SubnetDef { cidr: string; name: string | null }

function ipToNum(ip: string): number {
  return ip.split('.').reduce((acc, o) => (acc << 8) | parseInt(o), 0) >>> 0;
}

/** Return the user-defined network name for an IP, or null if unmatched / unnamed. */
export function getNetworkName(ip: string | null | undefined, subnets: SubnetDef[]): string | null {
  if (!ip || !subnets.length) return null;
  try {
    const ipNum = ipToNum(ip);
    for (const s of subnets) {
      if (!s.name) continue;
      const [net, prefStr] = s.cidr.split('/');
      const prefix = parseInt(prefStr);
      const mask = prefix === 0 ? 0 : (~0 << (32 - prefix)) >>> 0;
      if ((ipNum & mask) === (ipToNum(net) & mask)) return s.name;
    }
  } catch {}
  return null;
}

export function escHtml(str: string): string {
  if (!str) return '';
  return str.replace(/[&<>"']/g, (m) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[m] || m));
}
