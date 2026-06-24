import React, { useState, useEffect } from 'react';
import {
  Plus, Trash2, Edit2, Shield, Globe, Cpu, Save, X, Activity,
  Search, Play, CheckCircle2, AlertCircle, Loader2, Key, Database,
  Settings as SettingsIcon, Radar, Network, Wrench
} from 'lucide-react';

interface Device {
  id: number;
  hostname: string;
  ip_address: string;
  snmp_community: string;
  snmp_version: string;
  is_gateway: boolean;
  site?: string;
  poll_status: string;
  vendor?: string;
  model?: string;
  snmp_v3_username?: string;
  snmp_v3_auth_protocol?: string;
  snmp_v3_auth_password?: string;
  snmp_v3_priv_protocol?: string;
  snmp_v3_priv_password?: string;
  fortigate_api_key?: string;
  fortigate_port?: number;
}

interface ScanDiscovery {
  ip_address: string;
  sys_name?: string;
  sys_description?: string;
  vendor?: string;
  already_added: boolean;
}

export default function SettingsView({ apiFetch }: { apiFetch: (url: string, options?: any) => Promise<Response> }) {
  const [activeTab, setActiveTab] = useState<'list' | 'form' | 'scan' | 'networks' | 'maintenance'>('list');
  const [devices, setDevices] = useState<Device[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [testResult, setTestResult] = useState<{ok: boolean, msg: string} | null>(null);
  
  const [scanCidr, setScanCidr] = useState('');
  const [scanResults, setScanResults] = useState<ScanDiscovery[]>([]);
  const [isScanning, setIsScanning] = useState(false);

  const [formData, setFormData] = useState<any>({
    hostname: '',
    ip_address: '',
    snmp_community: 'public',
    snmp_version: '2c',
    is_gateway: false,
    site: '',
    snmp_v3_username: '',
    snmp_v3_auth_protocol: 'SHA',
    snmp_v3_auth_password: '',
    snmp_v3_priv_protocol: 'AES',
    snmp_v3_priv_password: '',
    fortigate_api_key: '',
    fortigate_port: 443
  });

  useEffect(() => {
    fetchDevices();
  }, []);

  const fetchDevices = async () => {
    try {
      const res = await apiFetch('/api/devices');
      const data = await res.json();
      setDevices(data);
    } catch (e) { console.error(e); }
  };

  const handleTestConnection = async () => {
    setIsLoading(true);
    setTestResult(null);
    try {
      const res = await apiFetch('/api/devices/test', {
        method: 'POST',
        body: JSON.stringify(formData)
      });
      const data = await res.json();
      if (data.reachable) {
        setTestResult({ ok: true, msg: `Connected! Detected ${data.vendor || 'Unknown'} device (${data.sys_name || 'No sysName'})` });
        if (!formData.hostname) setFormData({ ...formData, hostname: data.sys_name || '' });
      } else {
        setTestResult({ ok: false, msg: data.error || 'Connection failed' });
      }
    } catch (e: any) {
      setTestResult({ ok: false, msg: e.message });
    } finally {
      setIsLoading(false);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const method = editingId ? 'PUT' : 'POST';
      const url = editingId ? `/api/devices/${editingId}` : '/api/devices';
      
      const res = await apiFetch(url, {
        method,
        body: JSON.stringify(formData)
      });

      if (res.ok) {
        resetForm();
        fetchDevices();
        setActiveTab('list');
      } else {
        const err = await res.json();
        alert(err.detail || "Failed to save device");
      }
    } catch (e) { console.error(e); }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Are you sure you want to remove this device?")) return;
    try {
      await apiFetch(`/api/devices/${id}`, { method: 'DELETE' });
      fetchDevices();
    } catch (e) { console.error(e); }
  };

  const runScan = async () => {
    if (!scanCidr) return;
    setIsScanning(true);
    setScanResults([]);
    try {
      const res = await apiFetch('/api/devices/scan', {
        method: 'POST',
        body: JSON.stringify({ cidr: scanCidr, community: formData.snmp_community, version: formData.snmp_version })
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setScanResults(data);
    } catch (e: any) {
      alert("Scan failed: " + e.message);
    } finally {
      setIsScanning(false);
    }
  };

  const addFromScan = (found: ScanDiscovery) => {
    setFormData({
      ...formData,
      hostname: found.sys_name || found.ip_address,
      ip_address: found.ip_address,
    });
    setActiveTab('form');
    setEditingId(null);
  };

  const startEdit = (device: Device) => {
    setEditingId(device.id);
    setFormData({
      hostname: device.hostname,
      ip_address: device.ip_address,
      snmp_community: device.snmp_community,
      snmp_version: device.snmp_version,
      is_gateway: device.is_gateway,
      site: device.site || '',
      snmp_v3_username: device.snmp_v3_username || '',
      snmp_v3_auth_protocol: device.snmp_v3_auth_protocol || 'SHA',
      snmp_v3_auth_password: device.snmp_v3_auth_password || '',
      snmp_v3_priv_protocol: device.snmp_v3_priv_protocol || 'AES',
      snmp_v3_priv_password: device.snmp_v3_priv_password || '',
      fortigate_api_key: device.fortigate_api_key || '',
      fortigate_port: device.fortigate_port || 443
    });
    setActiveTab('form');
  };

  const resetForm = () => {
    setEditingId(null);
    setTestResult(null);
    setFormData({
      hostname: '', ip_address: '', snmp_community: 'public', snmp_version: '2c',
      is_gateway: false, site: '', snmp_v3_username: '', snmp_v3_auth_protocol: 'SHA',
      snmp_v3_auth_password: '', snmp_v3_priv_protocol: 'AES', snmp_v3_priv_password: '',
      fortigate_api_key: '', fortigate_port: 443
    });
  };

  return (
    <div className="flex flex-col h-full bg-bg/20 overflow-hidden">
      <div className="flex px-6 pt-4 gap-1 border-b border-border bg-surface/50 overflow-x-auto flex-shrink-0 scrollbar-none">
        <TabButton active={activeTab === 'list'} onClick={() => setActiveTab('list')} icon={<Database className="w-3.5 h-3.5" />} label="Devices" />
        <TabButton active={activeTab === 'form'} onClick={() => { if (activeTab !== 'form') resetForm(); setActiveTab('form'); }} icon={<Plus className="w-3.5 h-3.5" />} label={editingId ? "Edit" : "Add Manual"} />
        <TabButton active={activeTab === 'scan'} onClick={() => setActiveTab('scan')} icon={<Radar className="w-3.5 h-3.5" />} label="Scanner" />
        <TabButton active={activeTab === 'networks'} onClick={() => setActiveTab('networks')} icon={<Network className="w-3.5 h-3.5" />} label="Networks" />
        <TabButton active={activeTab === 'maintenance'} onClick={() => setActiveTab('maintenance')} icon={<Wrench className="w-3.5 h-3.5" />} label="Maintenance" />
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {activeTab === 'list' && (
          <div className="space-y-3">
            {devices.map(device => (
              <div key={device.id} className="group bg-surface border border-border hover:border-accent/30 rounded-xl p-4 transition-all flex items-center justify-between shadow-sm">
                <div className="flex items-center gap-4">
                  <div className={`p-2.5 rounded-lg ${device.poll_status === 'ok' ? 'bg-green/10 text-green' : 'bg-red/10 text-red'}`}>
                    <Globe className="w-4 h-4" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-white">{device.hostname}</span>
                      {device.is_gateway && <span className="text-[9px] bg-accent/10 text-accent px-1.5 py-0.5 rounded uppercase font-bold tracking-tighter">Gateway</span>}
                      <span className="text-[9px] bg-surface2 text-text2 px-1.5 py-0.5 rounded font-mono uppercase">{device.snmp_version === '3' ? 'SNMPv3' : `v${device.snmp_version}`}</span>
                    </div>
                    <div className="flex items-center gap-2 text-[10px] text-text2 font-medium mt-0.5">
                      <span>{device.ip_address}</span>
                      <span>•</span>
                      <span>{device.vendor || 'Unknown Vendor'} {device.model || ''}</span>
                      <span>•</span>
                      <span>Site: {device.site || 'Default'}</span>
                    </div>
                  </div>
                </div>
                <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button onClick={() => startEdit(device)} className="p-2 text-text2 hover:text-accent hover:bg-accent/10 rounded-lg transition-all"><Edit2 className="w-3.5 h-3.5" /></button>
                  <button onClick={() => handleDelete(device.id)} className="p-2 text-text2 hover:text-red hover:bg-red/10 rounded-lg transition-all"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              </div>
            ))}
          </div>
        )}

        {activeTab === 'form' && (
          <form onSubmit={handleSave} className="max-w-2xl space-y-8 animate-in fade-in duration-300 pb-12">
            <section className="space-y-4">
              <h4 className="text-[10px] font-bold text-text2 uppercase tracking-widest px-1">Network Identity</h4>
              <div className="grid grid-cols-2 gap-4">
                <FormField label="Hostname / Label" value={formData.hostname} onChange={(v: string) => setFormData({...formData, hostname: v})} placeholder="Core-SW-01" required />
                <FormField label="Management IP" value={formData.ip_address} onChange={(v: string) => setFormData({...formData, ip_address: v})} placeholder="10.60.x.x" required />
                <FormField label="Logical Site" value={formData.site} onChange={(v: string) => setFormData({...formData, site: v})} placeholder="Main Office" />
                <div className="flex items-end pb-2 px-1">
                  <label className="flex items-center gap-3 text-xs font-medium text-text cursor-pointer">
                    <input type="checkbox" checked={formData.is_gateway} onChange={e => setFormData({...formData, is_gateway: e.target.checked})} className="w-4 h-4 rounded border-border bg-bg text-accent focus:ring-accent" />
                    Set as Gateway
                  </label>
                </div>
              </div>
            </section>

            <section className="space-y-4">
              <div className="flex items-center justify-between">
                <h4 className="text-[10px] font-bold text-text2 uppercase tracking-widest px-1">SNMP Configuration</h4>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setFormData({...formData, snmp_version: '2c'})} className={`px-2 py-0.5 rounded text-[10px] font-bold ${formData.snmp_version === '2c' ? 'bg-accent text-white' : 'bg-surface2 text-text2'}`}>v2c</button>
                  <button type="button" onClick={() => setFormData({...formData, snmp_version: '3'})} className={`px-2 py-0.5 rounded text-[10px] font-bold ${formData.snmp_version === '3' ? 'bg-accent text-white' : 'bg-surface2 text-text2'}`}>v3</button>
                </div>
              </div>

              {formData.snmp_version === '3' ? (
                <div className="bg-surface2/50 border border-border rounded-xl p-5 space-y-4">
                  <FormField label="Security Username" value={formData.snmp_v3_username} onChange={(v: string) => setFormData({...formData, snmp_v3_username: v})} placeholder="snmp-admin" />
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1.5">
                      <label className="text-[10px] font-bold text-text2 uppercase px-1">Auth Protocol</label>
                      <select className="w-full bg-bg border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-accent" value={formData.snmp_v3_auth_protocol} onChange={e => setFormData({...formData, snmp_v3_auth_protocol: e.target.value})}>
                        <option value="SHA">SHA</option><option value="MD5">MD5</option><option value="SHA256">SHA-256</option>
                      </select>
                    </div>
                    <FormField label="Auth Password" value={formData.snmp_v3_auth_password} onChange={(v: string) => setFormData({...formData, snmp_v3_auth_password: v})} type="password" />
                    <div className="space-y-1.5">
                      <label className="text-[10px] font-bold text-text2 uppercase px-1">Priv Protocol</label>
                      <select className="w-full bg-bg border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-accent" value={formData.snmp_v3_priv_protocol} onChange={e => setFormData({...formData, snmp_v3_priv_protocol: e.target.value})}>
                        <option value="AES">AES-128</option><option value="AES256">AES-256</option><option value="DES">DES</option>
                      </select>
                    </div>
                    <FormField label="Priv Password" value={formData.snmp_v3_priv_password} onChange={(v: string) => setFormData({...formData, snmp_v3_priv_password: v})} type="password" />
                  </div>
                </div>
              ) : (
                <FormField label="Read Community String" value={formData.snmp_community} onChange={(v: string) => setFormData({...formData, snmp_community: v})} />
              )}
            </section>

            <section className="space-y-4">
              <h4 className="text-[10px] font-bold text-text2 uppercase tracking-widest px-1">Identity Integration (Optional)</h4>
              <div className="bg-surface2/30 border border-border rounded-xl p-5 grid grid-cols-2 gap-4">
                <FormField label="FortiGate API Key" value={formData.fortigate_api_key} onChange={(v: string) => setFormData({...formData, fortigate_api_key: v})} placeholder="For DHCP Sync" type="password" />
                <FormField label="API Port" value={formData.fortigate_port} onChange={(v: string) => setFormData({...formData, fortigate_port: parseInt(v) || 0})} type="number" />
              </div>
            </section>

            <div className="space-y-4 pt-4">
              {testResult && (
                <div className={`flex items-center gap-3 p-4 rounded-xl border text-xs animate-in slide-in-from-top-2 ${testResult.ok ? 'bg-green/10 border-green/20 text-green' : 'bg-red/10 border-red/20 text-red'}`}>
                  {testResult.ok ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                  {testResult.msg}
                </div>
              )}
              
              <div className="flex gap-3">
                <button type="submit" className="flex-1 flex items-center justify-center gap-2 py-3 bg-accent text-white rounded-xl text-xs font-bold uppercase shadow-lg shadow-accent/20 hover:opacity-90 transition-opacity">
                  <Save className="w-4 h-4" /> {editingId ? 'Update Switch' : 'Save Switch'}
                </button>
                <button type="button" onClick={handleTestConnection} disabled={isLoading || !formData.ip_address} className="px-6 flex items-center justify-center gap-2 py-3 bg-surface border border-border text-white rounded-xl text-xs font-bold uppercase hover:border-accent transition-all disabled:opacity-50">
                  {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Activity className="w-4 h-4" />} Test SNMP
                </button>
                <button type="button" onClick={() => { setActiveTab('list'); resetForm(); }} className="px-6 py-3 bg-surface2 border border-border text-text2 rounded-xl text-xs font-bold uppercase hover:text-white transition-all">Cancel</button>
              </div>
            </div>
          </form>
        )}

        {activeTab === 'networks' && (
          <NetworksTab apiFetch={apiFetch} />
        )}

        {activeTab === 'maintenance' && (
          <MaintenanceTab apiFetch={apiFetch} />
        )}

        {activeTab === 'scan' && (
          <div className="space-y-6 animate-in fade-in duration-300">
            <div className="bg-surface border border-border rounded-2xl p-6 shadow-xl">
              <h4 className="text-xs font-bold text-white uppercase mb-4 flex items-center gap-2"><Search className="w-4 h-4 text-accent" /> SNMP CIDR Scanner</h4>
              <p className="text-xs text-text2 mb-6">Enter a network range to automatically discover SNMP-capable switches. High-speed, multi-threaded probe.</p>
              <div className="flex gap-3">
                <input type="text" className="flex-1 bg-bg border border-border rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-accent" placeholder="e.g. 10.60.1.0/24" value={scanCidr} onChange={e => setScanCidr(e.target.value)} />
                <button onClick={runScan} disabled={isScanning || !scanCidr} className="px-8 flex items-center gap-2 py-3 bg-accent text-white rounded-xl text-xs font-bold uppercase shadow-lg shadow-accent/20 disabled:opacity-50">
                  {isScanning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Start Scan
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {scanResults.map((found, i) => (
                <div key={i} className="bg-surface border border-border rounded-xl p-4 flex items-center justify-between">
                  <div className="flex-1 min-w-0 pr-4">
                    <div className="font-bold text-white text-xs">{found.sys_name || found.ip_address}</div>
                    <div className="text-[10px] text-text2 font-mono mt-0.5">{found.ip_address}</div>
                    <div className="text-[10px] text-accent mt-1 uppercase font-bold">{found.vendor}</div>
                  </div>
                  <button onClick={() => addFromScan(found)} disabled={found.already_added} className={`px-3 py-1.5 rounded-lg text-[10px] font-bold uppercase transition-all ${found.already_added ? 'bg-green/10 text-green opacity-50 cursor-default' : 'bg-accent/10 text-accent hover:bg-accent text-white'}`}>
                    {found.already_added ? 'Added' : 'Import'}
                  </button>
                </div>
              ))}
            </div>
            
            {isScanning && (
              <div className="py-12 text-center border border-dashed border-border rounded-2xl">
                <Loader2 className="w-8 h-8 text-accent animate-spin mx-auto mb-3" />
                <p className="text-xs text-text2 font-medium">Probing network nodes...</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

interface SubnetRow { id: number; cidr: string; name: string | null }
interface Discovered { suggested_cidr: string; ip_count: number; sample_ips: string[]; already_configured: boolean }

function NetworksTab({ apiFetch }: { apiFetch: (url: string, options?: any) => Promise<Response> }) {
  const [subnets, setSubnets] = useState<SubnetRow[]>([]);
  const [discovered, setDiscovered] = useState<Discovered[]>([]);
  const [cidr, setCidr] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [loadingDiscover, setLoadingDiscover] = useState(false);

  useEffect(() => { fetchSubnets(); fetchDiscovered(); }, []);

  const fetchSubnets = async () => {
    try { const r = await apiFetch('/api/subnets'); setSubnets(await r.json()); } catch {}
  };
  const fetchDiscovered = async () => {
    setLoadingDiscover(true);
    try { const r = await apiFetch('/api/subnets/discovered'); setDiscovered(await r.json()); } catch {}
    finally { setLoadingDiscover(false); }
  };

  const handleAdd = async (c: string, n: string) => {
    setError('');
    try {
      const r = await apiFetch('/api/subnets', { method: 'POST', body: JSON.stringify({ cidr: c, name: n }) });
      if (!r.ok) { const d = await r.json(); setError(d.detail || 'Failed'); return; }
      setCidr(''); setName('');
      await fetchSubnets();
      await fetchDiscovered();
    } catch (e: any) { setError(e.message); }
  };

  const handleDelete = async (id: number) => {
    await apiFetch(`/api/subnets/${id}`, { method: 'DELETE' });
    await fetchSubnets();
    await fetchDiscovered();
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      {/* Configured subnets */}
      <section className="space-y-4">
        <h4 className="text-[10px] font-bold text-text2 uppercase tracking-widest">Defined Networks</h4>
        {subnets.length === 0 ? (
          <div className="py-8 border border-dashed border-border rounded-xl text-center text-text2 text-xs">
            No networks defined yet. Add one below or import from discovered.
          </div>
        ) : (
          <div className="space-y-2">
            {subnets.map(s => (
              <div key={s.id} className="group flex items-center justify-between bg-surface border border-border rounded-xl px-4 py-3">
                <div>
                  <span className="font-mono text-sm text-white">{s.cidr}</span>
                  {s.name && <span className="ml-3 text-xs text-text2">{s.name}</span>}
                </div>
                <button onClick={() => handleDelete(s.id)} className="opacity-0 group-hover:opacity-100 p-1.5 text-text2 hover:text-red hover:bg-red/10 rounded-lg transition-all">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Add form */}
        <div className="bg-surface2/40 border border-border rounded-xl p-5 space-y-4">
          <h5 className="text-[10px] font-bold text-text2 uppercase tracking-widest">Add Network</h5>
          <div className="flex gap-3">
            <div className="flex-1 space-y-1">
              <label className="text-[10px] font-bold text-text2 uppercase px-1">CIDR</label>
              <input
                className="w-full bg-bg border border-border rounded-xl px-3 py-2.5 text-xs text-white focus:outline-none focus:border-accent placeholder:text-text2/20"
                placeholder="10.60.0.0/22"
                value={cidr}
                onChange={e => setCidr(e.target.value)}
              />
            </div>
            <div className="flex-1 space-y-1">
              <label className="text-[10px] font-bold text-text2 uppercase px-1">Name (optional)</label>
              <input
                className="w-full bg-bg border border-border rounded-xl px-3 py-2.5 text-xs text-white focus:outline-none focus:border-accent placeholder:text-text2/20"
                placeholder="Main Office LAN"
                value={name}
                onChange={e => setName(e.target.value)}
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={() => handleAdd(cidr, name)}
                disabled={!cidr}
                className="flex items-center gap-2 px-5 py-2.5 bg-accent text-white rounded-xl text-xs font-bold uppercase shadow-lg shadow-accent/20 hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                <Plus className="w-3.5 h-3.5" /> Add
              </button>
            </div>
          </div>
          {error && <p className="text-xs text-red">{error}</p>}
        </div>
      </section>

      {/* Discovered /24 suggestions */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-[10px] font-bold text-text2 uppercase tracking-widest">Discovered from ARP Table</h4>
          <button onClick={fetchDiscovered} disabled={loadingDiscover} className="text-[10px] text-text2 hover:text-accent flex items-center gap-1 transition-colors">
            {loadingDiscover ? <Loader2 className="w-3 h-3 animate-spin" /> : <Activity className="w-3 h-3" />} Refresh
          </button>
        </div>
        <p className="text-xs text-text2">
          These /24 groups were inferred from your ARP data. If a group belongs to a larger subnet (e.g. /22), add the larger one above and it will replace all the overlapping groups.
        </p>
        {discovered.length === 0 && !loadingDiscover && (
          <div className="py-6 border border-dashed border-border rounded-xl text-center text-text2 text-xs">No ARP data yet.</div>
        )}
        <div className="space-y-2">
          {discovered.map(d => (
            <div key={d.suggested_cidr} className="flex items-center justify-between bg-surface border border-border rounded-xl px-4 py-3 gap-4">
              <div className="flex items-center gap-4 flex-1 min-w-0">
                <span className="font-mono text-sm text-white shrink-0">{d.suggested_cidr}</span>
                <span className="text-xs text-text2 shrink-0">{d.ip_count} IPs seen</span>
                <span className="text-[10px] text-text2/60 font-mono truncate">{d.sample_ips.join(', ')}{d.ip_count > 5 ? '…' : ''}</span>
              </div>
              {d.already_configured ? (
                <span className="shrink-0 text-[10px] bg-green/10 text-green px-2 py-1 rounded-lg font-bold uppercase">Covered</span>
              ) : (
                <button
                  onClick={() => handleAdd(d.suggested_cidr, '')}
                  className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 bg-surface2 border border-border text-text2 hover:text-white hover:border-accent rounded-lg text-[10px] font-bold uppercase transition-all"
                >
                  <Plus className="w-3 h-3" /> Add
                </button>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function MaintenanceTab({ apiFetch }: { apiFetch: (url: string, options?: any) => Promise<Response> }) {
  const [olderThanDays, setOlderThanDays] = useState(7);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<{ mac_entries_deleted: number; arp_entries_deleted: number } | null>(null);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);

  const handlePurge = async () => {
    if (!confirmed) { setConfirmed(true); return; }
    setIsRunning(true);
    setResult(null);
    setError('');
    try {
      const res = await apiFetch(`/api/devices/maintenance/stale-data?older_than_days=${olderThanDays}`, { method: 'DELETE' });
      if (!res.ok) { const d = await res.json(); setError(d.detail || 'Failed'); return; }
      setResult(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsRunning(false);
      setConfirmed(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="bg-surface border border-border rounded-2xl p-6 shadow-xl space-y-5">
        <div>
          <h4 className="text-xs font-bold text-white uppercase mb-1 flex items-center gap-2">
            <Trash2 className="w-4 h-4 text-red" /> Purge Stale MAC / IP Data
          </h4>
          <p className="text-xs text-text2">
            Removes inactive MAC and ARP entries that haven't been seen since the cutoff. Useful after decommissioning devices or clearing out old DHCP leases. Does not affect current poll data, port history, or labels.
          </p>
        </div>

        <div className="flex items-end gap-4">
          <div className="space-y-1.5">
            <label className="text-[10px] font-bold text-text2 uppercase tracking-widest px-1">Older than</label>
            <select
              className="bg-bg border border-border rounded-xl px-3 py-2.5 text-xs text-white focus:outline-none focus:border-accent"
              value={olderThanDays}
              onChange={e => { setOlderThanDays(parseInt(e.target.value)); setConfirmed(false); setResult(null); }}
            >
              <option value={1}>1 day</option>
              <option value={3}>3 days</option>
              <option value={7}>7 days</option>
              <option value={14}>14 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
          </div>

          <button
            onClick={handlePurge}
            disabled={isRunning}
            className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-bold uppercase tracking-wide transition-all disabled:opacity-50 ${
              confirmed
                ? 'bg-red text-white shadow-lg shadow-red/20 hover:opacity-90'
                : 'bg-surface2 border border-border text-text2 hover:border-red hover:text-red'
            }`}
          >
            {isRunning
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Purging...</>
              : confirmed
              ? <><Trash2 className="w-3.5 h-3.5" /> Confirm — delete stale entries</>
              : <><Trash2 className="w-3.5 h-3.5" /> Purge inactive entries</>
            }
          </button>
          {confirmed && !isRunning && (
            <button onClick={() => setConfirmed(false)} className="text-xs text-text2 hover:text-white transition-colors px-3">
              Cancel
            </button>
          )}
        </div>

        {result && (
          <div className="flex items-center gap-2 px-4 py-3 bg-green/10 border border-green/20 rounded-xl text-xs text-green font-medium">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            Purged {result.mac_entries_deleted} MAC entr{result.mac_entries_deleted === 1 ? 'y' : 'ies'} and {result.arp_entries_deleted} ARP entr{result.arp_entries_deleted === 1 ? 'y' : 'ies'}.
          </div>
        )}
        {error && (
          <div className="px-4 py-3 bg-red/10 border border-red/20 rounded-xl text-xs text-red">{error}</div>
        )}
      </div>
    </div>
  );
}

function TabButton({ active, onClick, icon, label }: any) {
  return (
    <button onClick={onClick} className={`flex-shrink-0 flex items-center gap-2 px-5 py-3 text-xs font-bold uppercase tracking-tight transition-all border-b-2 ${active ? 'border-accent text-white bg-accent/5' : 'border-transparent text-text2 hover:text-white'}`}>
      {icon} {label}
    </button>
  );
}

function FormField({ label, value, onChange, placeholder, type = 'text', required }: any) {
  return (
    <div className="space-y-1.5">
      <label className="text-[10px] font-bold text-text2 uppercase px-1">{label} {required && '*'}</label>
      <input 
        type={type}
        className="w-full bg-bg border border-border rounded-xl px-3 py-2.5 text-xs text-white focus:outline-none focus:border-accent transition-all placeholder:text-text2/20"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
      />
    </div>
  );
}
