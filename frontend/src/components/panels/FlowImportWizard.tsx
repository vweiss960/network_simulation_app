import { useState, useRef } from 'react';
import { useStore } from '../../stores/useStore';
import toast from 'react-hot-toast';
import { Upload, X, ArrowRight, FileSpreadsheet, AlertTriangle, Check, Table, ClipboardPaste } from 'lucide-react';
import * as XLSX from 'xlsx';

const FLOW_FIELDS = [
  { key: 'name', label: 'Flow Name', required: true },
  { key: 'source_node', label: 'Source Node', required: false, group: 'source' },
  { key: 'source_location', label: 'Source Location', required: false, group: 'source' },
  { key: 'dest_node', label: 'Destination Node', required: false, group: 'dest' },
  { key: 'dest_location', label: 'Destination Location', required: false, group: 'dest' },
  { key: 'bandwidth_gbps', label: 'Bandwidth (Gbps)', required: false },
  { key: 'routing_behavior', label: 'Routing Behavior', required: false },
  { key: 'ecmp_enabled', label: 'ECMP Enabled', required: false },
] as const;

type FlowFieldKey = typeof FLOW_FIELDS[number]['key'];

type DescriptionMode = 'none' | 'single' | 'multi';

interface Props {
  onClose: () => void;
}

export default function FlowImportWizard({ onClose }: Props) {
  const { nodes, flows, bulkAddFlows } = useStore();
  const fileInput = useRef<HTMLInputElement>(null);

  // Flow names compare trimmed and case-insensitively. This duplicates the
  // backend's rule (routes/flows.py _norm) — keep the two in step.
  const normName = (n: string) => n.trim().toLowerCase();
  // Rows are skipped when their name is inherited from a parent topology (the
  // child can't shadow it) or already exists in this topology (re-importing a
  // file must not create duplicates).
  const inheritedFlowNames = new Set(flows.filter(f => f.is_inherited).map(f => normName(f.name)));
  const localFlowNames = new Set(flows.filter(f => !f.is_inherited).map(f => normName(f.name)));

  // State
  const [step, setStep] = useState<'upload' | 'sheet' | 'map' | 'preview'>('upload');
  const [fileName, setFileName] = useState('');
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<Record<string, string>[]>([]);
  const [mapping, setMapping] = useState<Record<FlowFieldKey, string>>({
    name: '', source_node: '', source_location: '', dest_node: '', dest_location: '',
    bandwidth_gbps: '', routing_behavior: '', ecmp_enabled: '',
  });
  const [isImporting, setIsImporting] = useState(false);
  // Guards against a double-click submitting twice before the state updates.
  const importingRef = useRef(false);
  // Rows whose name already exists here: skip (default) or update that flow.
  const [updateExisting, setUpdateExisting] = useState(false);

  // Description mapping state
  const [descMode, setDescMode] = useState<DescriptionMode>('none');
  const [descSingleCol, setDescSingleCol] = useState('');
  const [descMultiCols, setDescMultiCols] = useState<string[]>([]);
  const [descDelimiter, setDescDelimiter] = useState(' - ');

  // Excel sheet state
  const [workbook, setWorkbook] = useState<XLSX.WorkBook | null>(null);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [selectedSheet, setSelectedSheet] = useState('');

  // Auto-detect column mappings from headers
  const autoDetectMappings = (cols: string[]) => {
    const autoMap: Record<FlowFieldKey, string> = {
      name: '', source_node: '', source_location: '', dest_node: '', dest_location: '',
      bandwidth_gbps: '', routing_behavior: '', ecmp_enabled: '',
    };
    const lower = (s: string) => s.toLowerCase().replace(/[_\- ]/g, '');
    cols.forEach(col => {
      const lc = lower(col);
      if (['name', 'flowname', 'demandname'].includes(lc)) autoMap.name = col;
      else if (['source', 'sourcenode', 'sourcenodeid', 'src', 'anode', 'a'].includes(lc)) autoMap.source_node = col;
      else if (['sourcelocation', 'srclocation', 'srcloc', 'sourceloc', 'alocation', 'aloc'].includes(lc)) autoMap.source_location = col;
      else if (['dest', 'destination', 'destnode', 'destnodeid', 'dst', 'znode', 'z', 'target'].includes(lc)) autoMap.dest_node = col;
      else if (['destlocation', 'destinationlocation', 'dstlocation', 'destloc', 'dstloc', 'zlocation', 'zloc'].includes(lc)) autoMap.dest_location = col;
      else if (['bandwidthgbps', 'bandwidth', 'bw', 'gbps', 'rate'].includes(lc)) autoMap.bandwidth_gbps = col;
      else if (['routingbehavior', 'routing', 'routetype', 'behavior'].includes(lc)) autoMap.routing_behavior = col;
      else if (['ecmpenabled', 'ecmp'].includes(lc)) autoMap.ecmp_enabled = col;
    });
    setMapping(autoMap);
  };

  // Parse a sheet from workbook into headers + rows
  const loadSheet = (wb: XLSX.WorkBook, sheetName: string) => {
    const sheet = wb.Sheets[sheetName];
    const json = XLSX.utils.sheet_to_json<Record<string, any>>(sheet, { defval: '' });
    if (!json.length) { toast.error(`Sheet "${sheetName}" has no data`); return; }
    const cols = Object.keys(json[0]);
    const parsed = json.map(r => {
      const obj: Record<string, string> = {};
      cols.forEach(c => { obj[c] = String(r[c] ?? ''); });
      return obj;
    });
    setHeaders(cols);
    setRows(parsed);
    setSelectedSheet(sheetName);
    autoDetectMappings(cols);
    setStep('map');
  };

  // Parse uploaded file
  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const isExcel = /\.xlsx?$/i.test(file.name);
      const isJson = file.name.endsWith('.json');

      if (isExcel) {
        // Excel file — read with xlsx
        const buffer = await file.arrayBuffer();
        const wb = XLSX.read(buffer, { type: 'array' });
        setFileName(file.name);
        setWorkbook(wb);
        setSheetNames(wb.SheetNames);

        if (wb.SheetNames.length === 1) {
          // Single sheet — skip sheet selection
          loadSheet(wb, wb.SheetNames[0]);
        } else {
          // Multiple sheets — show sheet picker
          setSelectedSheet(wb.SheetNames[0]);
          setStep('sheet');
        }
      } else if (isJson) {
        const text = await file.text();
        const json = JSON.parse(text);
        const arr = Array.isArray(json) ? json : json.flows || json.data || [];
        if (!arr.length) { toast.error('No data found in JSON'); return; }
        const cols = Object.keys(arr[0]);
        const parsed = arr.map((r: any) => {
          const obj: Record<string, string> = {};
          cols.forEach(c => { obj[c] = String(r[c] ?? ''); });
          return obj;
        });
        setFileName(file.name);
        setHeaders(cols);
        setRows(parsed);
        autoDetectMappings(cols);
        setStep('map');
      } else {
        // CSV
        const text = await file.text();
        const lines = text.trim().split('\n');
        if (lines.length < 2) { toast.error('File needs a header row and at least one data row'); return; }
        const cols = lines[0].split(',').map(h => h.trim().replace(/^["']|["']$/g, ''));
        const parsed = lines.slice(1).filter(l => l.trim()).map(line => {
          const values = line.split(',').map(v => v.trim().replace(/^["']|["']$/g, ''));
          const obj: Record<string, string> = {};
          cols.forEach((h, i) => { obj[h] = values[i] || ''; });
          return obj;
        });
        if (!parsed.length) { toast.error('No data rows found'); return; }
        setFileName(file.name);
        setHeaders(cols);
        setRows(parsed);
        autoDetectMappings(cols);
        setStep('map');
      }
    } catch {
      toast.error('Failed to parse file');
    }
    if (fileInput.current) fileInput.current.value = '';
  };

  // Handle paste from clipboard (e.g. copied cells from Excel)
  const handlePaste = async (e: React.ClipboardEvent) => {
    // Prefer HTML format (from Excel/Sheets) — xlsx can parse it reliably
    const html = e.clipboardData.getData('text/html');
    const text = e.clipboardData.getData('text/plain');
    if (!html && !text?.trim()) { toast.error('Nothing to paste'); return; }
    try {
      let cols: string[] = [];
      let parsed: Record<string, string>[] = [];

      if (html) {
        // Use xlsx to parse the HTML table — handles cell boundaries perfectly
        const wb = XLSX.read(html, { type: 'string' });
        const sheet = wb.Sheets[wb.SheetNames[0]];
        const json = XLSX.utils.sheet_to_json<Record<string, any>>(sheet, { defval: '' });
        if (!json.length) { toast.error('No data found in pasted content'); return; }
        cols = Object.keys(json[0]);
        parsed = json.map(r => {
          const obj: Record<string, string> = {};
          cols.forEach(c => { obj[c] = String(r[c] ?? '').trim(); });
          return obj;
        });
      } else {
        // Fallback: parse plain text (tab or comma delimited)
        const lines = text.replace(/\r\n/g, '\n').trim().split('\n');
        if (lines.length < 2) { toast.error('Paste needs a header row and at least one data row'); return; }
        const delimiter = lines[0].includes('\t') ? '\t' : ',';
        cols = lines[0].split(delimiter).map(h => h.trim().replace(/^["']|["']$/g, ''));
        parsed = lines.slice(1).filter(l => l.trim()).map(line => {
          const values = line.split(delimiter).map(v => v.trim().replace(/^["']|["']$/g, ''));
          const obj: Record<string, string> = {};
          cols.forEach((h, i) => { obj[h] = values[i] || ''; });
          return obj;
        });
      }

      if (!parsed.length) { toast.error('No data rows found'); return; }
      setFileName('Pasted data');
      setHeaders(cols);
      setRows(parsed);
      autoDetectMappings(cols);
      setStep('map');
    } catch {
      toast.error('Failed to parse pasted data');
    }
  };

  // Get a preview of rows for a given sheet (used in sheet picker)
  const getSheetPreview = (sheetName: string) => {
    if (!workbook) return { cols: [] as string[], rows: [] as string[][] };
    const sheet = workbook.Sheets[sheetName];
    const json = XLSX.utils.sheet_to_json<Record<string, any>>(sheet, { defval: '' });
    if (!json.length) return { cols: [], rows: [] };
    const cols = Object.keys(json[0]);
    const previewRows = json.slice(0, 3).map(r => cols.map(c => String(r[c] ?? '')));
    return { cols, rows: previewRows, totalRows: json.length };
  };

  // Parse bandwidth from various formats like "10G", "100 Mbps", "2.5 Gbps", "500M", etc.
  const parseBandwidth = (raw: string): number => {
    if (!raw) return 1.0;
    const s = raw.trim().toLowerCase();
    // Try plain number first (assume Gbps)
    const plain = parseFloat(s);
    if (!isNaN(plain) && /^[\d.]+$/.test(s)) return plain;
    // Match number + optional unit
    const m = s.match(/^([\d.]+)\s*(gbps|gb|g|mbps|mb|m|tbps|tb|t)?/i);
    if (!m) return 1.0;
    const num = parseFloat(m[1]);
    if (isNaN(num)) return 1.0;
    const unit = (m[2] || 'g').charAt(0).toLowerCase();
    if (unit === 'm') return num / 1000;
    if (unit === 't') return num * 1000;
    return num; // default: Gbps
  };

  // Resolve node name/id to node id (with trimming and fuzzy whitespace handling)
  const resolveNode = (value: string): number | null => {
    if (!value) return null;
    const trimmed = value.trim();
    if (!trimmed) return null;
    const asNum = parseInt(trimmed);
    if (!isNaN(asNum) && nodes.find(n => n.id === asNum)) return asNum;
    // Exact match (case-insensitive, trimmed)
    const exact = nodes.find(n => n.name.trim().toLowerCase() === trimmed.toLowerCase());
    if (exact) return exact.id;
    // Fuzzy: collapse whitespace and compare
    const normalize = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ');
    const fuzzy = nodes.find(n => normalize(n.name) === normalize(trimmed));
    return fuzzy?.id ?? null;
  };

  // Resolve source/dest using node or location mapping
  // Uses a counter map to round-robin across nodes at a location
  const resolveEndpoint = (
    row: Record<string, string>,
    nodeKey: FlowFieldKey,
    locationKey: FlowFieldKey,
    locationCounters: Map<string, number>,
  ): { id: number | null; raw: string; resolvedVia: 'node' | 'location' | null; nodeName?: string } => {
    // Try resolving a value as a location via round-robin
    const tryLocationResolve = (locVal: string) => {
      const locLower = locVal.trim().toLowerCase();
      const matches = nodes.filter(n => n.location && n.location.trim().toLowerCase() === locLower);
      if (!matches.length) return null;
      const counter = locationCounters.get(locLower) ?? 0;
      const pick = matches[counter % matches.length];
      locationCounters.set(locLower, counter + 1);
      return { id: pick.id, raw: locVal, resolvedVia: 'location' as const, nodeName: pick.name };
    };

    // Prefer node mapping if provided and resolves successfully
    const nodeVal = mapping[nodeKey] ? row[mapping[nodeKey]] || '' : '';
    if (nodeVal) {
      const id = resolveNode(nodeVal);
      if (id !== null) return { id, raw: nodeVal, resolvedVia: 'node' };
      // Node didn't resolve — try this value as a location before moving on
      const asLoc = tryLocationResolve(nodeVal);
      if (asLoc) return asLoc;
    }
    // Fall back to explicit location column mapping with round-robin
    const locVal = mapping[locationKey] ? row[mapping[locationKey]] || '' : '';
    if (locVal) {
      const resolved = tryLocationResolve(locVal);
      if (resolved) return resolved;
      return { id: null, raw: locVal, resolvedVia: null };
    }
    return { id: null, raw: '', resolvedVia: null };
  };

  // Build preview data from mapping
  const getMappedFlows = () => {
    // Separate counters for source and dest so they distribute independently
    const sourceCounters = new Map<string, number>();
    const destCounters = new Map<string, number>();
    // Names seen on earlier rows: a repeat within the file is imported once.
    const seenInFile = new Set<string>();

    return rows.map((row, i) => {
      const name = (mapping.name ? row[mapping.name] || '' : `Flow ${i + 1}`).trim();
      const source = resolveEndpoint(row, 'source_node', 'source_location', sourceCounters);
      const dest = resolveEndpoint(row, 'dest_node', 'dest_location', destCounters);
      const bw = mapping.bandwidth_gbps ? parseBandwidth(row[mapping.bandwidth_gbps]) : 1.0;
      const routing = mapping.routing_behavior ? (row[mapping.routing_behavior] || 'shortest_path') : 'shortest_path';
      const ecmpRaw = mapping.ecmp_enabled ? row[mapping.ecmp_enabled] : '';
      const ecmp = ['true', '1', 'yes'].includes(ecmpRaw.toLowerCase());

      let description = '';
      if (descMode === 'single' && descSingleCol) {
        description = row[descSingleCol] || '';
      } else if (descMode === 'multi' && descMultiCols.length > 0) {
        description = descMultiCols.map(c => row[c] || '').filter(Boolean).join(descDelimiter);
      }

      const key = normName(name);
      const endpointsResolved = !!name && source.id !== null && dest.id !== null;
      // 'existing' rows are skipped, or update the flow when "Update existing"
      // is on. Only a row that can actually import claims its name, so a bad
      // row doesn't make a later good one with the same name look repeated.
      const skip: 'parent' | 'existing' | 'repeat' | null = !name ? null
        : inheritedFlowNames.has(key) ? 'parent'
        : seenInFile.has(key) ? 'repeat'
        : localFlowNames.has(key) ? 'existing' : null;
      if (endpointsResolved && !skip || skip === 'existing') seenInFile.add(key);
      return {
        row: i,
        name,
        description,
        sourceRaw: source.raw,
        destRaw: dest.raw,
        sourceId: source.id,
        destId: dest.id,
        sourceVia: source.resolvedVia,
        destVia: dest.resolvedVia,
        sourceNodeName: source.nodeName,
        destNodeName: dest.nodeName,
        bandwidth_gbps: bw,
        routing_behavior: routing,
        ecmp_enabled: ecmp,
        skip,
        endpointsResolved,
        valid: endpointsResolved && !skip,
      };
    });
  };

  // Resolved flows are locked in when entering preview (so random picks don't change on re-render)
  const [resolvedFlows, setResolvedFlows] = useState<ReturnType<typeof getMappedFlows>>([]);

  // For the map step, compute on the fly for validation; for preview, use locked-in state
  const mappedFlows = step === 'preview' ? resolvedFlows : (step === 'map' ? getMappedFlows() : []);
  // A row imports when it's valid, or when it updates an existing flow.
  const willUpdate = (f: { skip: string | null; endpointsResolved: boolean }) =>
    updateExisting && f.skip === 'existing' && f.endpointsResolved;
  const importable = (f: { valid: boolean; skip: string | null; endpointsResolved: boolean }) => f.valid || willUpdate(f);
  const validCount = mappedFlows.filter(importable).length;
  const invalidCount = mappedFlows.length - validCount;
  const conflictCount = mappedFlows.filter(f => f.skip === 'parent').length;
  const existingCount = mappedFlows.filter(f => f.skip === 'existing').length;
  const repeatCount = mappedFlows.filter(f => f.skip === 'repeat').length;
  const unresolvedCount = mappedFlows.filter(f => !f.endpointsResolved && f.skip !== 'parent' && f.skip !== 'repeat').length;
  const hasSourceMapping = mapping.source_node || mapping.source_location;
  const hasDestMapping = mapping.dest_node || mapping.dest_location;
  const requiredMapped = mapping.name && hasSourceMapping && hasDestMapping;

  // Lock in random assignments and move to preview
  const goToPreview = () => {
    setResolvedFlows(getMappedFlows());
    setStep('preview');
  };

  // Navigate back
  const goBack = () => {
    if (step === 'preview') { setResolvedFlows([]); setStep('map'); }
    else if (step === 'map' && sheetNames.length > 1) setStep('sheet');
    else setStep('upload');
  };

  // Import
  const handleImport = async () => {
    if (importingRef.current) return;
    const toImport = mappedFlows.filter(importable).map(f => ({
      name: f.name,
      description: f.description || '',
      source_node_id: f.sourceId!,
      dest_node_id: f.destId!,
      bandwidth_gbps: f.bandwidth_gbps,
      routing_behavior: f.routing_behavior,
      ecmp_enabled: f.ecmp_enabled,
      explicit_path: [],
    }));
    if (!toImport.length) { toast.error('No valid flows to import'); return; }
    importingRef.current = true;
    setIsImporting(true);
    try {
      // Count from the server's answer: it may skip names this preview
      // didn't know about (e.g. added by someone else since the page loaded).
      const before = new Set(flows.map(f => f.id));
      const saved = await bulkAddFlows(toImport, updateExisting);
      const updated = saved.filter(f => before.has(f.id)).length;
      const created = saved.length - updated;
      const skipped = rows.length - saved.length;
      toast.success([
        `Imported ${created} flow${created === 1 ? '' : 's'}`,
        updated ? `${updated} updated` : '',
        skipped ? `${skipped} skipped` : '',
      ].filter(Boolean).join(' · '));
      onClose();
    } catch (e: any) {
      // Surface backend detail (e.g. parent-flow name conflict listing the offending names)
      let msg = 'Failed to import flows';
      try {
        const body = e?.message?.split(': ').slice(1).join(': ');
        const parsed = body ? JSON.parse(body) : null;
        if (parsed?.detail && typeof parsed.detail === 'string') msg = parsed.detail;
      } catch { /* fall through */ }
      toast.error(msg, { duration: 8000 });
    }
    importingRef.current = false;
    setIsImporting(false);
  };

  // Step labels for indicator
  const allSteps = sheetNames.length > 1
    ? ['Upload File', 'Select Sheet', 'Map Columns', 'Preview & Import'] as const
    : ['Upload File', 'Map Columns', 'Preview & Import'] as const;
  const stepToIndex: Record<string, number> = sheetNames.length > 1
    ? { upload: 0, sheet: 1, map: 2, preview: 3 }
    : { upload: 0, map: 1, preview: 2 };
  const currentStepIndex = stepToIndex[step] ?? 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-surface-800 border border-surface-600 rounded-xl shadow-2xl w-[680px] max-h-[80vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-surface-700">
          <div className="flex items-center gap-2">
            <FileSpreadsheet size={18} className="text-primary-400" />
            <span className="text-sm font-semibold text-surface-100">Import Demands from Spreadsheet</span>
          </div>
          <button className="btn btn-ghost btn-sm p-1" onClick={onClose}><X size={16} /></button>
        </div>

        {/* Steps indicator */}
        <div className="flex items-center gap-2 px-5 py-2 border-b border-surface-700/50 text-[11px]">
          {allSteps.map((label, i) => {
            const isActive = i === currentStepIndex;
            const isDone = currentStepIndex > i;
            return (
              <div key={label} className="flex items-center gap-2">
                {i > 0 && <ArrowRight size={10} className="text-surface-600" />}
                <span className={`px-2 py-0.5 rounded-full ${
                  isActive ? 'bg-primary-600 text-white' :
                  isDone ? 'bg-primary-900/40 text-primary-300' :
                  'bg-surface-700 text-surface-500'
                }`}>{label}</span>
              </div>
            );
          })}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">

          {/* Step: Upload */}
          {step === 'upload' && (
            <div className="flex flex-col items-center justify-center py-8 space-y-6">
              {/* File upload option */}
              <div className="flex flex-col items-center space-y-3">
                <div className="w-14 h-14 rounded-full bg-surface-700 flex items-center justify-center">
                  <Upload size={24} className="text-surface-400" />
                </div>
                <div className="text-center">
                  <p className="text-sm text-surface-200">Upload a spreadsheet file</p>
                  <p className="text-xs text-surface-500 mt-1">Supports Excel (.xlsx, .xls), CSV, and JSON</p>
                </div>
                <button className="btn btn-primary" onClick={() => fileInput.current?.click()}>
                  Choose File
                </button>
                <input ref={fileInput} type="file" accept=".csv,.json,.xlsx,.xls" className="hidden" onChange={handleFile} />
              </div>

              <div className="flex items-center gap-3 w-full max-w-xs">
                <div className="flex-1 border-t border-surface-700" />
                <span className="text-[10px] text-surface-600 uppercase tracking-wide">or</span>
                <div className="flex-1 border-t border-surface-700" />
              </div>

              {/* Paste option */}
              <div className="flex flex-col items-center space-y-3 w-full max-w-md">
                <div className="w-14 h-14 rounded-full bg-surface-700 flex items-center justify-center">
                  <ClipboardPaste size={24} className="text-surface-400" />
                </div>
                <div className="text-center">
                  <p className="text-sm text-surface-200">Paste from Excel</p>
                  <p className="text-xs text-surface-500 mt-1">
                    Select cells in your spreadsheet (including headers), copy, then click below and paste
                  </p>
                </div>
                <div
                  contentEditable
                  onPaste={handlePaste}
                  onInput={(e) => { (e.target as HTMLElement).textContent = ''; }}
                  className="w-full h-20 rounded-lg border-2 border-dashed border-surface-600 bg-surface-900/50
                    flex items-center justify-center text-xs text-surface-500 cursor-text
                    focus:border-primary-500 focus:outline-none transition-colors"
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                  role="textbox"
                  tabIndex={0}
                  suppressContentEditableWarning
                >
                  Click here and press Ctrl+V to paste
                </div>
              </div>
            </div>
          )}

          {/* Step: Sheet selection (Excel only) */}
          {step === 'sheet' && (
            <div className="space-y-4">
              <div>
                <p className="text-sm text-surface-200">
                  <span className="text-primary-300 font-medium">{fileName}</span> contains {sheetNames.length} sheets
                </p>
                <p className="text-xs text-surface-500 mt-1">Select the sheet that contains your demand data</p>
              </div>

              <div className="space-y-2">
                {sheetNames.map(name => {
                  const preview = getSheetPreview(name);
                  const isSelected = selectedSheet === name;
                  return (
                    <div
                      key={name}
                      className={`rounded-lg border p-3 cursor-pointer transition-colors ${
                        isSelected
                          ? 'border-primary-500 bg-primary-900/20'
                          : 'border-surface-700 bg-surface-800/50 hover:border-surface-500'
                      }`}
                      onClick={() => setSelectedSheet(name)}
                    >
                      <div className="flex items-center gap-2 mb-2">
                        <Table size={14} className={isSelected ? 'text-primary-400' : 'text-surface-500'} />
                        <span className={`text-sm font-medium ${isSelected ? 'text-primary-300' : 'text-surface-200'}`}>
                          {name}
                        </span>
                        <span className="text-[10px] text-surface-500">
                          {preview.totalRows ?? 0} rows &middot; {preview.cols.length} columns
                        </span>
                      </div>
                      {preview.cols.length > 0 && (
                        <div className="overflow-x-auto rounded border border-surface-700/50">
                          <table className="w-full text-[10px]">
                            <thead>
                              <tr className="bg-surface-700/30">
                                {preview.cols.map(c => (
                                  <th key={c} className="text-left px-1.5 py-1 text-surface-500 font-medium whitespace-nowrap">{c}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {preview.rows.slice(0, 2).map((row, i) => (
                                <tr key={i} className="border-t border-surface-700/30">
                                  {row.map((val, j) => (
                                    <td key={j} className="px-1.5 py-0.5 text-surface-400 whitespace-nowrap max-w-[120px] truncate">{val}</td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                      {preview.cols.length === 0 && (
                        <p className="text-[10px] text-surface-600 italic">Empty sheet</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Step: Map columns */}
          {step === 'map' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-surface-200">
                    Map columns from <span className="text-primary-300 font-medium">{fileName}</span>
                    {sheetNames.length > 1 && <span className="text-surface-500"> &middot; sheet "{selectedSheet}"</span>}
                  </p>
                  <p className="text-xs text-surface-500">{rows.length} rows found &middot; {headers.length} columns detected</p>
                </div>
              </div>

              {/* Column mapping */}
              <div className="space-y-2">
                {FLOW_FIELDS.map((field, idx) => {
                  // Add group labels before source/dest groups
                  const showGroupLabel =
                    (field.group === 'source' && FLOW_FIELDS[idx - 1]?.group !== 'source') ||
                    (field.group === 'dest' && FLOW_FIELDS[idx - 1]?.group !== 'dest');
                  const groupLabel = field.group === 'source' ? 'Source (map node or location)' :
                    field.group === 'dest' ? 'Destination (map node or location)' : null;
                  const isGroupRequired = field.group === 'source'
                    ? !mapping.source_node && !mapping.source_location
                    : field.group === 'dest'
                      ? !mapping.dest_node && !mapping.dest_location
                      : false;

                  return (
                    <div key={field.key}>
                      {showGroupLabel && (
                        <div className="flex items-center gap-2 mt-3 mb-1">
                          <span className="text-[10px] font-medium uppercase tracking-wide text-surface-400">
                            {groupLabel}
                          </span>
                          {isGroupRequired && <span className="text-[9px] text-red-400">* at least one required</span>}
                          <div className="flex-1 border-t border-surface-700/50" />
                        </div>
                      )}
                      <div className="flex items-center gap-3">
                        <div className="w-[180px] text-xs text-right">
                          <span className={field.required ? 'text-surface-200 font-medium' : 'text-surface-400'}>
                            {field.label}
                          </span>
                          {field.required && <span className="text-red-400 ml-1">*</span>}
                        </div>
                        <ArrowRight size={12} className="text-surface-600 shrink-0" />
                        <select
                          className="flex-1 text-sm"
                          value={mapping[field.key]}
                          onChange={e => setMapping({ ...mapping, [field.key]: e.target.value })}
                        >
                          <option value="">— Don't map —</option>
                          {headers.map(h => (
                            <option key={h} value={h}>{h}</option>
                          ))}
                        </select>
                        {mapping[field.key] && (
                          <span className="text-[10px] text-surface-500 w-[140px] truncate" title={rows[0]?.[mapping[field.key]] || ''}>
                            e.g. "{rows[0]?.[mapping[field.key]] || ''}"
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Description mapping */}
              <div className="mt-3 mb-1 flex items-center gap-2">
                <span className="text-[10px] font-medium uppercase tracking-wide text-surface-400">Description</span>
                <div className="flex-1 border-t border-surface-700/50" />
              </div>
              <div className="space-y-2">
                <div className="flex gap-2">
                  {(['none', 'single', 'multi'] as DescriptionMode[]).map(mode => (
                    <button
                      key={mode}
                      className={`btn btn-sm text-[10px] flex-1 ${descMode === mode ? 'bg-primary-600 text-white' : 'btn-secondary'}`}
                      onClick={() => setDescMode(mode)}
                    >
                      {mode === 'none' ? 'None' : mode === 'single' ? 'Single Column' : 'Multiple Columns'}
                    </button>
                  ))}
                </div>
                {descMode === 'single' && (
                  <div className="flex items-center gap-3">
                    <div className="w-[180px] text-xs text-right text-surface-400">Description Column</div>
                    <ArrowRight size={12} className="text-surface-600 shrink-0" />
                    <select className="flex-1 text-sm" value={descSingleCol} onChange={e => setDescSingleCol(e.target.value)}>
                      <option value="">— Select column —</option>
                      {headers.map(h => <option key={h} value={h}>{h}</option>)}
                    </select>
                    {descSingleCol && (
                      <span className="text-[10px] text-surface-500 w-[140px] truncate" title={rows[0]?.[descSingleCol] || ''}>
                        e.g. "{rows[0]?.[descSingleCol] || ''}"
                      </span>
                    )}
                  </div>
                )}
                {descMode === 'multi' && (
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <div className="w-[180px] text-xs text-right text-surface-400">Delimiter</div>
                      <ArrowRight size={12} className="text-surface-600 shrink-0" />
                      <input className="w-24 text-sm font-mono" value={descDelimiter} onChange={e => setDescDelimiter(e.target.value)} />
                      <span className="text-[10px] text-surface-500">between each field</span>
                    </div>
                    <div className="flex items-start gap-3">
                      <div className="w-[180px] text-xs text-right text-surface-400 pt-1">Select Columns</div>
                      <ArrowRight size={12} className="text-surface-600 shrink-0 mt-1.5" />
                      <div className="flex-1 flex flex-wrap gap-1.5">
                        {headers.map(h => {
                          const selected = descMultiCols.includes(h);
                          const idx = descMultiCols.indexOf(h);
                          return (
                            <button
                              key={h}
                              className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
                                selected
                                  ? 'bg-primary-600/30 border-primary-500 text-primary-300'
                                  : 'bg-surface-700/50 border-surface-600 text-surface-400 hover:border-surface-500'
                              }`}
                              onClick={() => {
                                if (selected) setDescMultiCols(descMultiCols.filter(c => c !== h));
                                else setDescMultiCols([...descMultiCols, h]);
                              }}
                            >
                              {selected && <span className="text-[8px] mr-1">{idx + 1}.</span>}
                              {h}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    {descMultiCols.length > 0 && rows[0] && (
                      <div className="text-[10px] text-surface-500 pl-[196px]">
                        Preview: "{descMultiCols.map(c => rows[0][c] || '').filter(Boolean).join(descDelimiter)}"
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Location info note */}
              {(mapping.source_location || mapping.dest_location) && (
                <div className="text-[10px] text-surface-500 bg-surface-900/50 rounded p-2">
                  When using location mapping, demands are distributed evenly (round-robin) across nodes at each location.
                  If both node and location are mapped, the node column takes priority.
                </div>
              )}

              {/* Sample data preview */}
              <div className="mt-4">
                <p className="text-[11px] text-surface-500 font-medium uppercase tracking-wide mb-2">Sample data (first 3 rows)</p>
                <div className="overflow-x-auto rounded border border-surface-700">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="bg-surface-700/50">
                        {headers.map(h => (
                          <th key={h} className="text-left px-2 py-1.5 text-surface-400 font-medium whitespace-nowrap">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.slice(0, 3).map((row, i) => (
                        <tr key={i} className="border-t border-surface-700/50">
                          {headers.map(h => (
                            <td key={h} className="px-2 py-1 text-surface-300 whitespace-nowrap">{row[h]}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* Step: Preview */}
          {step === 'preview' && (
            <div className="space-y-4">
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-2 text-xs">
                  <Check size={14} className="text-emerald-400" />
                  <span className="text-emerald-300">{validCount} valid</span>
                </div>
                {unresolvedCount > 0 && (
                  <div className="flex items-center gap-2 text-xs">
                    <AlertTriangle size={14} className="text-amber-400" />
                    <span className="text-amber-300">{unresolvedCount} skipped (unresolved nodes/locations)</span>
                  </div>
                )}
                {conflictCount > 0 && (
                  <div className="flex items-center gap-2 text-xs">
                    <AlertTriangle size={14} className="text-cyan-400" />
                    <span className="text-cyan-300">{conflictCount} skipped (name conflicts with parent flow)</span>
                  </div>
                )}
                {existingCount > 0 && (
                  <div className="flex items-center gap-2 text-xs">
                    <AlertTriangle size={14} className="text-surface-400" />
                    <span className="text-surface-300">
                      {existingCount} {updateExisting ? 'will update existing flows' : 'skipped (already in this topology)'}
                    </span>
                  </div>
                )}
                {repeatCount > 0 && (
                  <div className="flex items-center gap-2 text-xs">
                    <AlertTriangle size={14} className="text-surface-400" />
                    <span className="text-surface-300">{repeatCount} skipped (repeated in file)</span>
                  </div>
                )}
              </div>

              {existingCount > 0 && (
                <label className="flex items-center gap-2 text-xs text-surface-300 cursor-pointer select-none">
                  <input type="checkbox" checked={updateExisting} onChange={e => setUpdateExisting(e.target.checked)} />
                  Update existing flows with the same name
                  <span className="text-surface-500">(otherwise they're skipped)</span>
                </label>
              )}

              <div className="overflow-x-auto rounded border border-surface-700 max-h-[360px] overflow-y-auto">
                <table className="w-full text-[11px]">
                  <thead className="sticky top-0">
                    <tr className="bg-surface-700">
                      <th className="text-left px-2 py-1.5 text-surface-400 font-medium"></th>
                      <th className="text-left px-2 py-1.5 text-surface-400 font-medium">Name</th>
                      {descMode !== 'none' && <th className="text-left px-2 py-1.5 text-surface-400 font-medium">Description</th>}
                      <th className="text-left px-2 py-1.5 text-surface-400 font-medium">Source</th>
                      <th className="text-left px-2 py-1.5 text-surface-400 font-medium">Destination</th>
                      <th className="text-left px-2 py-1.5 text-surface-400 font-medium">Bandwidth</th>
                      <th className="text-left px-2 py-1.5 text-surface-400 font-medium">Routing</th>
                      <th className="text-left px-2 py-1.5 text-surface-400 font-medium">ECMP</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mappedFlows.map((f, i) => (
                      <tr key={i} className={`border-t border-surface-700/50 ${
                        f.skip === 'parent' ? 'bg-cyan-900/10' : willUpdate(f) ? '' : f.skip ? 'bg-surface-700/20 opacity-70' : (!f.valid ? 'bg-red-900/10' : '')
                      }`}>
                        <td className="px-2 py-1">
                          {importable(f)
                            ? <Check size={12} className="text-emerald-500" />
                            : <AlertTriangle size={12} className={f.skip === 'parent' ? 'text-cyan-400' : f.skip ? 'text-surface-400' : 'text-amber-500'} />
                          }
                        </td>
                        <td className="px-2 py-1 text-surface-300">
                          {f.name || <span className="text-red-400">—</span>}
                          {f.skip === 'parent' && (
                            <span
                              className="ml-1.5 text-[9px] bg-cyan-600/30 text-cyan-300 border border-cyan-500/40 px-1 py-0.5 rounded"
                              title="A flow with this name is inherited from a parent topology"
                            >
                              CONFLICTS WITH PARENT
                            </span>
                          )}
                          {f.skip === 'existing' && (willUpdate(f) ? (
                            <span
                              className="ml-1.5 text-[9px] bg-primary-600/25 text-primary-200 border border-primary-500/40 px-1 py-0.5 rounded"
                              title="A flow with this name already exists in this topology — it will be updated from this row"
                            >
                              UPDATE
                            </span>
                          ) : (
                            <span
                              className="ml-1.5 text-[9px] bg-surface-600/40 text-surface-300 border border-surface-500/40 px-1 py-0.5 rounded"
                              title="A flow with this name already exists in this topology — skipped so re-importing doesn't create duplicates"
                            >
                              EXISTS
                            </span>
                          ))}
                          {f.skip === 'repeat' && (
                            <span
                              className="ml-1.5 text-[9px] bg-surface-600/40 text-surface-300 border border-surface-500/40 px-1 py-0.5 rounded"
                              title="This name appears on an earlier row of the file — only the first is imported"
                            >
                              REPEAT
                            </span>
                          )}
                        </td>
                        {descMode !== 'none' && (
                          <td className="px-2 py-1 text-surface-500 max-w-[150px] truncate" title={f.description}>{f.description || '—'}</td>
                        )}
                        <td className="px-2 py-1">
                          {f.sourceId !== null
                            ? <span className="text-surface-300">
                                {nodes.find(n => n.id === f.sourceId)?.name}
                                {f.sourceVia === 'location' && (
                                  <span className="text-[9px] text-primary-400 ml-1" title={`Selected from location "${f.sourceRaw}"`}>
                                    @ {f.sourceRaw}
                                  </span>
                                )}
                              </span>
                            : <span className="text-red-400" title="Not found">"{f.sourceRaw}"</span>
                          }
                        </td>
                        <td className="px-2 py-1">
                          {f.destId !== null
                            ? <span className="text-surface-300">
                                {nodes.find(n => n.id === f.destId)?.name}
                                {f.destVia === 'location' && (
                                  <span className="text-[9px] text-primary-400 ml-1" title={`Selected from location "${f.destRaw}"`}>
                                    @ {f.destRaw}
                                  </span>
                                )}
                              </span>
                            : <span className="text-red-400" title="Not found">"{f.destRaw}"</span>
                          }
                        </td>
                        <td className="px-2 py-1 text-surface-300">{f.bandwidth_gbps} Gbps</td>
                        <td className="px-2 py-1 text-surface-300">{f.routing_behavior}</td>
                        <td className="px-2 py-1 text-surface-300">{f.ecmp_enabled ? 'Yes' : 'No'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-surface-700">
          <div>
            {step !== 'upload' && (
              <button className="btn btn-secondary btn-sm" onClick={goBack}>
                Back
              </button>
            )}
          </div>
          <div className="flex gap-2">
            <button className="btn btn-secondary btn-sm" onClick={onClose}>Cancel</button>
            {step === 'sheet' && (
              <button
                className="btn btn-primary btn-sm"
                disabled={!selectedSheet}
                onClick={() => loadSheet(workbook!, selectedSheet)}
              >
                Next: Map Columns <ArrowRight size={12} />
              </button>
            )}
            {step === 'map' && (
              <button
                className="btn btn-primary btn-sm"
                disabled={!requiredMapped}
                onClick={goToPreview}
              >
                Next: Preview <ArrowRight size={12} />
              </button>
            )}
            {step === 'preview' && (
              <button
                className="btn btn-primary btn-sm"
                disabled={validCount === 0 || isImporting}
                onClick={handleImport}
              >
                {isImporting ? 'Importing...' : `Import ${validCount} Flows`}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
