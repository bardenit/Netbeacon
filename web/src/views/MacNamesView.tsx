import { useState, useEffect, useRef } from 'react';
import { Tag, Plus, Pencil, Trash2, Search, X, Check, Loader2 } from 'lucide-react';

interface LabelEntry {
  id: number;
  mac_address: string;
  vendor?: string | null;
  label: string;
  notes?: string | null;
}

interface MacNamesViewProps {
  apiFetch: (url: string, options?: any) => Promise<Response>;
  prefillMac?: string | null;
  onPrefillConsumed?: () => void;
  onLabelsChanged?: () => void;
  onSearch?: (q: string) => void;
  refreshKey?: number;
}

export default function MacNamesView({ apiFetch, prefillMac, onPrefillConsumed, onLabelsChanged, onSearch, refreshKey }: MacNamesViewProps) {
  const [labels, setLabels] = useState<LabelEntry[]>([]);
  const [filter, setFilter] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editEntry, setEditEntry] = useState<LabelEntry | null>(null);
  const [formMac, setFormMac] = useState('');
  const [formVendor, setFormVendor] = useState('');
  const [formLabel, setFormLabel] = useState('');
  const [formNotes, setFormNotes] = useState('');
  const [vendorLoading, setVendorLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const macInputRef = useRef<HTMLInputElement>(null);
  const vendorLookupTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = async () => {
    try {
      const res = await apiFetch('/api/labels');
      if (res.ok) setLabels(await res.json());
    } catch (e) {}
  };

  useEffect(() => { load(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (prefillMac) {
      openAdd(prefillMac);
      onPrefillConsumed?.();
    }
  }, [prefillMac]);

  const openAdd = (mac = '') => {
    setEditEntry(null);
    setFormMac(mac);
    setFormLabel('');
    setFormNotes('');
    setFormVendor('');
    setShowForm(true);
    if (mac) lookupVendor(mac);
    setTimeout(() => macInputRef.current?.focus(), 50);
  };

  const openEdit = (entry: LabelEntry) => {
    setEditEntry(entry);
    setFormMac(entry.mac_address);
    setFormLabel(entry.label);
    setFormNotes(entry.notes || '');
    setFormVendor(entry.vendor || '');
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    setEditEntry(null);
    setFormMac('');
    setFormLabel('');
    setFormNotes('');
    setFormVendor('');
  };

  const lookupVendor = async (mac: string) => {
    const normalized = mac.replace(/[-. ]/g, ':').toLowerCase();
    // Need at least 3 octets to look up OUI
    const octets = normalized.split(':').filter(o => o.length === 2);
    if (octets.length < 3) { setFormVendor(''); return; }
    setVendorLoading(true);
    try {
      const res = await apiFetch(`/api/labels/oui?mac=${encodeURIComponent(normalized)}`);
      if (res.ok) {
        const { vendor } = await res.json();
        setFormVendor(vendor || '');
      }
    } catch (e) {}
    setVendorLoading(false);
  };

  const handleMacChange = (val: string) => {
    setFormMac(val);
    if (vendorLookupTimer.current) clearTimeout(vendorLookupTimer.current);
    vendorLookupTimer.current = setTimeout(() => lookupVendor(val), 400);
  };

  const handleSave = async () => {
    const mac = formMac.trim().toLowerCase().replace(/[-. ]/g, ':');
    if (!mac || !formLabel.trim()) return;
    setSaving(true);
    try {
      await apiFetch('/api/labels', {
        method: 'POST',
        body: JSON.stringify({ mac_address: mac, label: formLabel.trim(), notes: formNotes.trim() || null }),
      });
      await load();
      onLabelsChanged?.();
      closeForm();
    } catch (e) {}
    setSaving(false);
  };

  const handleDelete = async (mac: string) => {
    try {
      await apiFetch(`/api/labels/${encodeURIComponent(mac)}`, { method: 'DELETE' });
      await load();
      onLabelsChanged?.();
      setDeleteConfirm(null);
    } catch (e) {}
  };

  const filtered = labels.filter(l => {
    const q = filter.toLowerCase();
    return !q || l.mac_address.includes(q) || l.label.toLowerCase().includes(q) ||
      (l.vendor || '').toLowerCase().includes(q) || (l.notes || '').toLowerCase().includes(q);
  });

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-4">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Tag className="w-5 h-5 text-accent" />
            <h2 className="text-base font-semibold text-white">MAC Names</h2>
            <span className="text-xs text-text2">{labels.length} entr{labels.length === 1 ? 'y' : 'ies'}</span>
          </div>
          <button
            onClick={() => openAdd()}
            className="flex items-center gap-2 px-3.5 py-1.5 bg-accent text-white rounded-md text-xs font-bold uppercase tracking-wide hover:opacity-90 transition-opacity"
          >
            <Plus className="w-3.5 h-3.5" /> Add Entry
          </button>
        </div>

        {/* Add / Edit Form */}
        {showForm && (
          <div className="bg-surface border border-accent/30 rounded-lg p-5 shadow-lg shadow-accent/5 animate-in fade-in slide-in-from-top-2 duration-200">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-white">{editEntry ? 'Edit Entry' : 'Add MAC Name'}</h3>
              <button onClick={closeForm} className="text-text2 hover:text-white"><X className="w-4 h-4" /></button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-[10px] font-bold text-text2 uppercase tracking-wider mb-1.5">MAC Address</label>
                <input
                  ref={macInputRef}
                  value={formMac}
                  onChange={e => handleMacChange(e.target.value)}
                  disabled={!!editEntry}
                  placeholder="aa:bb:cc:dd:ee:ff"
                  className="w-full bg-bg border border-border rounded-md px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-accent transition-colors disabled:opacity-50"
                />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-text2 uppercase tracking-wider mb-1.5">
                  Vendor
                  {vendorLoading && <Loader2 className="inline w-3 h-3 ml-1 animate-spin" />}
                </label>
                <input
                  value={formVendor}
                  readOnly
                  placeholder="Auto-detected from OUI…"
                  className="w-full bg-bg border border-border rounded-md px-3 py-2 text-xs text-text2 focus:outline-none cursor-default"
                />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-text2 uppercase tracking-wider mb-1.5">Name <span className="text-red">*</span></label>
                <input
                  value={formLabel}
                  onChange={e => setFormLabel(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleSave()}
                  placeholder="e.g. Jason's MacBook"
                  className="w-full bg-bg border border-border rounded-md px-3 py-2 text-xs text-white focus:outline-none focus:border-accent transition-colors"
                />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-text2 uppercase tracking-wider mb-1.5">Notes</label>
                <input
                  value={formNotes}
                  onChange={e => setFormNotes(e.target.value)}
                  placeholder="Optional description…"
                  className="w-full bg-bg border border-border rounded-md px-3 py-2 text-xs text-white focus:outline-none focus:border-accent transition-colors"
                />
              </div>
            </div>
            <div className="flex gap-2 mt-4 justify-end">
              <button onClick={closeForm} className="px-4 py-1.5 text-xs border border-border rounded-md text-text2 hover:text-white hover:border-accent transition-colors">Cancel</button>
              <button
                onClick={handleSave}
                disabled={saving || !formMac.trim() || !formLabel.trim()}
                className="flex items-center gap-1.5 px-4 py-1.5 text-xs bg-accent text-white rounded-md hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                Save
              </button>
            </div>
          </div>
        )}

        {/* Filter */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text2" />
          <input
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder="Filter by MAC, name, vendor, or notes…"
            className="w-full bg-surface border border-border rounded-lg pl-9 pr-4 py-2 text-xs text-white focus:outline-none focus:border-accent transition-colors"
          />
          {filter && (
            <button onClick={() => setFilter('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-text2 hover:text-white">
              <X className="w-3 h-3" />
            </button>
          )}
        </div>

        {/* Table */}
        {filtered.length === 0 ? (
          <div className="text-center py-16 text-text2">
            {labels.length === 0
              ? <><Tag className="w-8 h-8 mx-auto mb-3 opacity-30" /><p className="text-sm">No MAC names yet.</p><p className="text-xs mt-1">Add entries here or click the tag icon next to any MAC address.</p></>
              : <p className="text-sm">No entries match "{filter}".</p>}
          </div>
        ) : (
          <div className="bg-surface border border-border rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border bg-surface2">
                  <th className="text-left px-4 py-2.5 text-[10px] font-bold text-text2 uppercase tracking-wider">MAC Address</th>
                  <th className="text-left px-4 py-2.5 text-[10px] font-bold text-text2 uppercase tracking-wider">Vendor</th>
                  <th className="text-left px-4 py-2.5 text-[10px] font-bold text-text2 uppercase tracking-wider">Name</th>
                  <th className="text-left px-4 py-2.5 text-[10px] font-bold text-text2 uppercase tracking-wider">Notes</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((entry, i) => (
                  <tr key={entry.mac_address} className={`border-b border-border/50 hover:bg-surface2/50 transition-colors ${i % 2 === 0 ? '' : 'bg-surface2/20'}`}>
                    <td className="px-4 py-3 font-mono text-text">{entry.mac_address}</td>
                    <td className="px-4 py-3 text-text2">{entry.vendor || <span className="opacity-40">—</span>}</td>
                    <td className="px-4 py-3">
                      <span
                        onClick={() => onSearch?.(entry.label)}
                        className={`px-2 py-0.5 bg-accent/10 border border-accent/20 text-accent rounded font-medium ${onSearch ? 'cursor-pointer hover:bg-accent/20 transition-colors' : ''}`}
                        title={onSearch ? `Search for "${entry.label}"` : undefined}
                      >{entry.label}</span>
                    </td>
                    <td className="px-4 py-3 text-text2 max-w-[200px] truncate">{entry.notes || <span className="opacity-40">—</span>}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2 justify-end">
                        {deleteConfirm === entry.mac_address ? (
                          <>
                            <span className="text-red text-[10px]">Delete?</span>
                            <button onClick={() => handleDelete(entry.mac_address)} className="text-red hover:text-red/80"><Check className="w-3.5 h-3.5" /></button>
                            <button onClick={() => setDeleteConfirm(null)} className="text-text2 hover:text-white"><X className="w-3.5 h-3.5" /></button>
                          </>
                        ) : (
                          <>
                            <button onClick={() => openEdit(entry)} className="text-text2 hover:text-accent transition-colors"><Pencil className="w-3.5 h-3.5" /></button>
                            <button onClick={() => setDeleteConfirm(entry.mac_address)} className="text-text2 hover:text-red transition-colors"><Trash2 className="w-3.5 h-3.5" /></button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
