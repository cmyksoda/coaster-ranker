
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Undo2, RotateCcw, ListOrdered, Copy, Check, ArrowLeft, Trophy, Upload, Edit3, X, Moon, Sun } from 'lucide-react';
import Papa from 'papaparse';
import { useDropzone } from 'react-dropzone';
import { saveImages, loadImages, clearImages } from './imageStore';

const STORAGE_KEY = 'coaster-ranking-v1';
const COASTER_LIST_KEY = 'coaster-list-v1';

// localStorage only ever sees the metadata; image references go to IndexedDB.
function withoutImages(coasters) {
  return coasters.map(({ image, ...rest }) => rest);
}

function shuffleIndices(n) {
  const arr = Array.from({ length: n }, (_, i) => i);
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function initRuntime_full(coasters) {
  const queue = coasters.map(c => [c]);
  let state = { mode: 'full', queue, currentMerge: null, comparisonCount: 0, done: false, finalRanking: null };
  if (state.queue.length >= 2) {
    const left = state.queue.shift();
    const right = state.queue.shift();
    state.currentMerge = { left, right, leftIdx: 0, rightIdx: 0, result: [] };
  } else if (state.queue.length === 1) {
    state.done = true;
    state.finalRanking = state.queue[0];
  } else {
    state.done = true;
    state.finalRanking = [];
  }
  return state;
}

function applyChoice_full(state, choice) {
  if (!state.currentMerge) return state;
  if (choice !== 'l' && choice !== 'r') return state;
  let queue = [...state.queue];
  let merge = {
    left: state.currentMerge.left,
    right: state.currentMerge.right,
    leftIdx: state.currentMerge.leftIdx,
    rightIdx: state.currentMerge.rightIdx,
    result: [...state.currentMerge.result],
  };
  if (choice === 'l') {
    merge.result.push(merge.left[merge.leftIdx]);
    merge.leftIdx++;
  } else {
    merge.result.push(merge.right[merge.rightIdx]);
    merge.rightIdx++;
  }
  let comparisonCount = state.comparisonCount + 1;
  let currentMerge = merge;
  let done = false;
  let finalRanking = null;
  while (currentMerge) {
    if (currentMerge.leftIdx >= currentMerge.left.length) {
      const finalRun = [...currentMerge.result, ...currentMerge.right.slice(currentMerge.rightIdx)];
      queue.push(finalRun);
      currentMerge = null;
    } else if (currentMerge.rightIdx >= currentMerge.right.length) {
      const finalRun = [...currentMerge.result, ...currentMerge.left.slice(currentMerge.leftIdx)];
      queue.push(finalRun);
      currentMerge = null;
    } else {
      break;
    }
    if (queue.length >= 2) {
      const left = queue.shift();
      const right = queue.shift();
      currentMerge = { left, right, leftIdx: 0, rightIdx: 0, result: [] };
    } else if (queue.length === 1) {
      done = true;
      finalRanking = queue[0];
      break;
    }
  }
  return { mode: 'full', queue, currentMerge, comparisonCount, done, finalRanking };
}

function getCurrentComparison_full(state) {
  if (!state.currentMerge) return null;
  return {
    left: state.currentMerge.left[state.currentMerge.leftIdx],
    right: state.currentMerge.right[state.currentMerge.rightIdx],
  };
}

function _topKState(k, topK, pending, ev, comparisonCount, done, finalRanking) {
  return { mode: 'topK', k, topK, pending, ev, comparisonCount, done, finalRanking };
}

function _startNextEval(state) {
  if (state.pending.length === 0) {
    return { ...state, ev: null, done: true, finalRanking: state.topK };
  }
  const newItem = state.pending[0];
  const newPending = state.pending.slice(1);
  if (state.topK.length === 0) {
    return _startNextEval({ ...state, topK: [newItem], pending: newPending });
  }
  const ev = (state.topK.length >= state.k)
    ? { newItem, phase: 'vsBottom' }
    : { newItem, phase: 'binarySearch', lo: 0, hi: state.topK.length };
  return { ...state, pending: newPending, ev };
}

function initRuntime_topK(coasters, k) {
  if (coasters.length === 0) return _topKState(k, [], [], null, 0, true, []);
  if (coasters.length === 1) return _topKState(k, coasters, [], null, 0, true, coasters);
  return _startNextEval(_topKState(k, [coasters[0]], coasters.slice(1), null, 0, false, null));
}

function applyChoice_topK(state, choice) {
  if (!state.ev || state.done) return state;
  if (choice !== 'l' && choice !== 'r') return state;
  const newCount = state.comparisonCount + 1;
  const ev = state.ev;

  if (ev.phase === 'vsBottom') {
    if (choice === 'r') {
      return _startNextEval({ ...state, comparisonCount: newCount });
    }
    const newTopK = state.topK.slice(0, -1);
    return {
      ...state,
      topK: newTopK,
      ev: { newItem: ev.newItem, phase: 'binarySearch', lo: 0, hi: newTopK.length },
      comparisonCount: newCount,
    };
  }

  if (ev.phase === 'binarySearch') {
    const { lo, hi, newItem } = ev;
    const mid = Math.floor((lo + hi) / 2);
    const newLo = choice === 'l' ? lo : mid + 1;
    const newHi = choice === 'l' ? mid : hi;
    if (newLo >= newHi) {
      const newTopK = [...state.topK.slice(0, newLo), newItem, ...state.topK.slice(newLo)];
      return _startNextEval({ ...state, topK: newTopK, comparisonCount: newCount });
    }
    return {
      ...state,
      ev: { newItem, phase: 'binarySearch', lo: newLo, hi: newHi },
      comparisonCount: newCount,
    };
  }
  return state;
}

function getCurrentComparison_topK(state) {
  if (!state.ev) return null;
  const ev = state.ev;
  if (ev.phase === 'vsBottom') {
    return { left: ev.newItem, right: state.topK[state.topK.length - 1] };
  }
  if (ev.phase === 'binarySearch') {
    const mid = Math.floor((ev.lo + ev.hi) / 2);
    return { left: ev.newItem, right: state.topK[mid] };
  }
  return null;
}

function initRuntime(coasters, mode, k) {
  if (mode === 'topK') return initRuntime_topK(coasters, k);
  return initRuntime_full(coasters);
}

function applyChoice(state, choice) {
  if (!state) return state;
  if (state.mode === 'topK') return applyChoice_topK(state, choice);
  return applyChoice_full(state, choice);
}

function getCurrentComparison(state) {
  if (!state) return null;
  if (state.mode === 'topK') return getCurrentComparison_topK(state);
  return getCurrentComparison_full(state);
}

function isValidSaveData(d, coastLen) {
  if (!d || !Array.isArray(d.initialShuffle) || !Array.isArray(d.choices)) return false;
  if (!d.mode || typeof d.k !== 'number') return false;
  if (d.initialShuffle.length !== coastLen) return false;
  const mockCoasters = Array.from({ length: coastLen }, (_, i) => ({ id: i }));
  const sim = reconstructState(d.initialShuffle, d.choices, d.mode, d.k, mockCoasters);
  return sim !== null && !sim.error;
}

function reconstructState(initialShuffle, choices, mode, k, coasters) {
  const ordered = initialShuffle.map(i => coasters[i]).filter(Boolean);
  let state = initRuntime(ordered, mode, k);
  for (const c of choices) state = applyChoice(state, c);
  return state;
}

const SECONDS_PER_PICK = 4;

function estimateComparisons(n, mode, k) {
  if (n <= 1) return 0;
  if (mode === 'topK') {
    const kk = Math.min(k, n);
    const log2k = Math.ceil(Math.log2(Math.max(2, kk)));
    const bootstrap = Math.ceil(kk * log2k);
    const wins = (n > kk) ? Math.ceil(kk * Math.log(n / kk)) : 0;
    const steady = (n - kk) + wins * log2k;
    return Math.round(bootstrap + steady);
  }
  return Math.ceil(n * Math.log2(n));
}

function estimateMinutes(comparisons) {
  return Math.max(1, Math.ceil(comparisons * SECONDS_PER_PICK / 60));
}

export default function App() {
  const [coasters, setCoasters] = useState(null);
  const [saveData, setSaveData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState('setup');
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [copied, setCopied] = useState(false);
  const [keyPulse, setKeyPulse] = useState(0);
  const [fetchingState, setFetchingState] = useState({ active: false, current: 0, total: 0 });
  const [theme, setTheme] = useState(() => localStorage.getItem('cr-theme') || 'light');

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('cr-theme', theme);
  }, [theme]);


  const runtimeState = useMemo(() => {
    if (!saveData || !coasters) return null;
    return reconstructState(saveData.initialShuffle, saveData.choices, saveData.mode, saveData.k, coasters);
  }, [saveData, coasters]);

  const currentComparison = useMemo(() => getCurrentComparison(runtimeState), [runtimeState]);

  useEffect(() => {
    (async () => {
      try {
        const storedCoasters = localStorage.getItem(COASTER_LIST_KEY);
        if (storedCoasters) {
          const parsedC = JSON.parse(storedCoasters);
          const images = await loadImages();
          setCoasters(parsedC.map(c => {
            const img = images.get(String(c.id));
            return img ? { ...c, image: img } : c;
          }));
          setView('compare');

          const result = localStorage.getItem(STORAGE_KEY);
          if (result) {
            const parsed = JSON.parse(result);
            if (isValidSaveData(parsed, parsedC.length)) {
              setSaveData(parsed);
            }
          }
        }
      } catch (e) {}
      setLoading(false);
    })();
  }, []);

  // Single place that persists the list, so no render path can throw on a
  // failed write. Skips the very first pass while the list is still loading.
  useEffect(() => {
    if (loading || !coasters) return;
    try {
      localStorage.setItem(COASTER_LIST_KEY, JSON.stringify(withoutImages(coasters)));
    } catch (e) {}
    saveImages(coasters);
  }, [coasters, loading]);

  useEffect(() => {
    if (runtimeState && runtimeState.done && view === 'compare') {
      setView('final');
    }
  }, [runtimeState, view]);
  
  const updateCoaster = useCallback((id, updates) => {
    setCoasters(prev => prev ? prev.map(c => c.id === id ? { ...c, ...updates } : c) : prev);
  }, []);

  function startWithMode(mode, k) {
    const initialShuffle = shuffleIndices(coasters.length);
    const data = { mode, k: mode === 'topK' ? k : 0, initialShuffle, choices: [] };
    setSaveData(data);
    persist(data);
    setView('compare');
  }

  async function persist(data) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch (e) {}
  }

  function handleChoice(choice) {
    if (!runtimeState || runtimeState.done || !currentComparison || runtimeState.error) return;
    setSaveData(prev => {
      if (!prev || prev.choices.length !== saveData.choices.length) return prev;
      const next = { ...prev, choices: [...prev.choices, choice] };
      persist(next);
      return next;
    });
    setKeyPulse(k => k + 1);
  }

  function handleUndo() {
    if (!saveData || saveData.choices.length === 0) return;
    const next = { ...saveData, choices: saveData.choices.slice(0, -1) };
    setSaveData(next);
    persist(next);
    if (view === 'final') setView('compare');
  }

  function handleReset() {
    setSaveData(null);
    setShowResetConfirm(false);
    setView('compare');
    localStorage.removeItem(STORAGE_KEY);
  }
  
  function handleResetAll() {
    setCoasters(null);
    setSaveData(null);
    setShowResetConfirm(false);
    setView('setup');
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(COASTER_LIST_KEY);
    clearImages();
  }

  function flashCopied() {
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  function handleCopy() {
    if (!runtimeState || !runtimeState.finalRanking) return;
    copyToClipboard(formatRanking(runtimeState.finalRanking, saveData.mode, saveData.k));
    flashCopied();
  }

  function handleCopyCSV() {
    if (!runtimeState || !runtimeState.finalRanking) return;
    copyToClipboard(formatCSV(runtimeState.finalRanking, saveData.mode, saveData.k));
    flashCopied();
  }

  useEffect(() => {
    function onKey(e) {
      if (view !== 'compare') return;
      if (!runtimeState || runtimeState.done) return;
      if (e.key === 'ArrowLeft' || e.key === '1') { e.preventDefault(); handleChoice('l'); }
      else if (e.key === 'ArrowRight' || e.key === '2') { e.preventDefault(); handleChoice('r'); }
      else if (e.key === 'u' || e.key === 'U') { e.preventDefault(); handleUndo(); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [view, runtimeState, saveData]);

  if (loading) {
    return (
      <div className="cr-app" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="cr-meta">Loading...</div>
      </div>
    );
  }
  
  if (view === 'fetching') {
    const pct = Math.round((fetchingState.current / fetchingState.total) * 100) || 0;
    return (
      <div className="cr-app" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
        <h2 className="cr-display" style={{fontSize: 40}}>LOADING<span className="cr-dots"></span></h2>
        <div className="cr-meta" style={{marginBottom: 20}}>Fetching images and details</div>
        <div className="cr-progress-track" style={{width: 300, maxWidth: '80%'}}>
          <div className="cr-progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="cr-meta" style={{marginTop: 10}}>{fetchingState.current} / {fetchingState.total} Coasters</div>
      </div>
    );
  }


  if (view === 'rank-more') {
    return (
      <div className="cr-app">
        <div className="cr-grid-bg" />
        <div className="cr-glow-bg" />
        <div style={{ position: 'relative', maxWidth: '900px', margin: '0 auto', padding: '20px 16px 32px' }}>
          <header style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 22 }}>
            <h1 className="cr-display" style={{ fontSize: 26, letterSpacing: '0.05em', margin: 0 }}>
              COASTER<span className="cr-text-red">/</span>RANKER
            </h1>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              {theme === 'light' ? 
                <Moon size={18} style={{cursor: 'pointer'}} onClick={() => setTheme('dark')} /> : 
                <Sun size={18} style={{cursor: 'pointer'}} onClick={() => setTheme('light')} />}
              <div className="cr-meta">N={coasters.length}</div>
            </div>
          </header>
          <button onClick={() => setView('compare')} className="cr-btn" style={{marginBottom: 20}}>← Cancel</button>
          <ModeSelector 
            n={coasters.length} 
            minK={saveData.mode === 'topK' ? saveData.k : 9999}
            onStart={(newMode, newK) => {
              const newSave = { ...saveData, mode: newMode, k: newK };
              setSaveData(newSave);
              persist(newSave);
              setView('compare');
            }} 
          />
        </div>
      </div>
    );
  }

  if (view === 'setup' || !coasters) {
    return (
      <div className="cr-app">
        <div className="cr-grid-bg" />
        <div className="cr-glow-bg" />
        <div style={{ position: 'relative', maxWidth: '900px', margin: '0 auto', padding: '20px 16px 32px' }}>
          <header style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 22 }}>
            <h1 className="cr-display" style={{ fontSize: 26, letterSpacing: '0.05em', margin: 0 }}>
              COASTER<span className="cr-text-red">/</span>RANKER
            </h1>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              {theme === 'light' ? 
                <Moon size={18} style={{cursor: 'pointer'}} onClick={() => setTheme('dark')} /> : 
                <Sun size={18} style={{cursor: 'pointer'}} onClick={() => setTheme('light')} />}

            </div>
          </header>
          <SetupView onReady={async (c) => {
            setFetchingState({ active: true, current: 0, total: c.length });
            setView('fetching');
            let enriched = [...c];
            
            for (let i = 0; i < c.length; i += 3) {
              const chunk = c.slice(i, i+3);
              const promises = chunk.map(coaster => 
                fetch('/api/fetch_coaster', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify(coaster)
                }).then(r => r.json())
                .then(data => {
                   setFetchingState(prev => ({...prev, current: prev.current + 1}));
                   if (data.image || data.type) {
                     return { ...coaster, image: data.image || null, type: data.type || coaster.type, fetched: true };
                   }
                   return { ...coaster, fetched: true };
                }).catch(() => {
                   setFetchingState(prev => ({...prev, current: prev.current + 1}));
                   return { ...coaster, fetched: true };
                })
              );
              const results = await Promise.all(promises);
              for (let j = 0; j < results.length; j++) {
                enriched[i+j] = results[j];
              }
              setCoasters([...enriched]);
            }
            setFetchingState({ active: false, current: 0, total: 0 });
            setView('compare'); 
          }} />
        </div>
      </div>
    );
  }

  if (!saveData || !runtimeState) {
    return (
      <div className="cr-app">
        <div className="cr-grid-bg" />
        <div className="cr-glow-bg" />
        <div style={{ position: 'relative', maxWidth: '900px', margin: '0 auto', padding: '20px 16px 32px' }}>
          <header style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 22 }}>
            <h1 className="cr-display" style={{ fontSize: 26, letterSpacing: '0.05em', margin: 0 }}>
              COASTER<span className="cr-text-red">/</span>RANKER
            </h1>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              {theme === 'light' ? 
                <Moon size={18} style={{cursor: 'pointer'}} onClick={() => setTheme('dark')} /> : 
                <Sun size={18} style={{cursor: 'pointer'}} onClick={() => setTheme('light')} />}

              <div className="cr-meta">N={coasters.length}</div>
            </div>
          </header>
          <button onClick={handleResetAll} className="cr-btn" style={{marginBottom: 20}}>← Change List</button>
          <ModeSelector n={coasters.length} onStart={startWithMode} />
        </div>
      </div>
    );
  }

  const total = estimateComparisons(coasters.length, saveData.mode, saveData.k);
  const count = runtimeState.comparisonCount;
  const pct = Math.min(100, total > 0 ? (count / total) * 100 : 100);
  const modeLabel = saveData.mode === 'topK' ? `TOP ${saveData.k}` : 'FULL';

  return (
    <div className="cr-app">
      <div className="cr-grid-bg" />
      <div className="cr-glow-bg" />
      <div style={{ position: 'relative', maxWidth: '1100px', margin: '0 auto', padding: '20px 16px 32px' }}>
        <header style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 22 }}>
          <h1 className="cr-display" style={{ fontSize: 26, letterSpacing: '0.05em', margin: 0 }}>
            COASTER<span className="cr-text-red">/</span>RANKER
          </h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              {theme === 'light' ? 
                <Moon size={18} style={{cursor: 'pointer'}} onClick={() => setTheme('dark')} /> : 
                <Sun size={18} style={{cursor: 'pointer'}} onClick={() => setTheme('light')} />}

            <div className="cr-meta">{modeLabel} · N={coasters.length}</div>
          </div>
        </header>

        <div style={{ marginBottom: 22 }}>
          <div className="cr-meta" style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <span>Comparisons</span>
            <span key={keyPulse} className="cr-pulse">{count} / ~{total}</span>
          </div>
          <div className="cr-progress-track">
            <div className="cr-progress-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>

        {view === 'compare' && currentComparison && currentComparison.left && currentComparison.right && (
          <CompareView
            left={currentComparison.left}
            right={currentComparison.right}
            onChoose={handleChoice}
            keyPulse={keyPulse}
            onUpdateCoaster={updateCoaster}
          />
        )}

        {view === 'standings' && (
          <StandingsView state={runtimeState} onBack={() => setView('compare')} coasters={coasters} />
        )}

        {view === 'final' && runtimeState.done && (
                    <FinalView 
            ranking={runtimeState.finalRanking}
            mode={saveData.mode}
            k={saveData.k}
            totalN={coasters.length}
            count={runtimeState.comparisonCount}
            onCopy={handleCopy}
            onCopyCSV={handleCopyCSV}
            copied={copied}
            onReset={() => setShowResetConfirm(true)}
            onUndo={handleUndo}
            onRankMore={() => setView('rank-more')}
          />
        )}

        {view === 'compare' && (
          <footer style={{ marginTop: 24, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <button onClick={handleUndo} disabled={saveData.choices.length === 0} className="cr-btn">
              <Undo2 size={12} /> Undo
            </button>
            <button onClick={() => setView('standings')} className="cr-btn">
              <ListOrdered size={12} /> Standings
            </button>
            <button onClick={() => setShowResetConfirm(true)} className="cr-btn danger">
              <RotateCcw size={12} /> Reset
            </button>
          </footer>
        )}

        {view === 'compare' && count === 0 && (
          <div className="cr-meta" style={{ textAlign: 'center', marginTop: 20, opacity: 0.55 }}>
            Tap the coaster you prefer · auto-saves
          </div>
        )}
      </div>

      {showResetConfirm && (
        <div className="cr-modal-bg" onClick={() => setShowResetConfirm(false)}>
          <div className="cr-modal" onClick={e => e.stopPropagation()}>
            <h3 className="cr-display" style={{ fontSize: 22, letterSpacing: '0.05em', margin: '0 0 12px' }}>RESET ALL PROGRESS?</h3>
            <p className="cr-text-soft" style={{ fontSize: 14, margin: '0 0 20px', lineHeight: 1.5 }}>
              This erases all {count} comparisons.
            </p>
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => setShowResetConfirm(false)} className="cr-btn" style={{ flex: 1, justifyContent: 'center' }}>Cancel</button>
              <button onClick={handleResetAll} className="cr-btn danger" style={{ flex: 1, justifyContent: 'center' }}>New List</button>
              <button onClick={handleReset} className="cr-btn reset-primary" style={{ flex: 1, justifyContent: 'center' }}>Reset Rank</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SetupView({ onReady }) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  
  const processText = (content) => {
    let parsed = [];
    if (content.trim().startsWith('[') || content.trim().startsWith('{')) {
      try {
        const json = JSON.parse(content);
        if (Array.isArray(json)) {
          parsed = json.map((c, i) => ({
            id: c.id || i+1,
            name: c.name || 'Unknown',
            park: c.park || '',
            type: c.type || ''
          }));
        }
      } catch (e) {
        setError('Invalid JSON format');
        return;
      }
    } else {
      const result = Papa.parse(content, { header: true, skipEmptyLines: true });
      if (result.data && result.data.length > 0 && result.data[0].name) {
        parsed = result.data.map((c, i) => ({
          id: i+1,
          name: c.name || c.Name || c.coaster || c.Coaster || 'Unknown',
          park: c.park || c.Park || '',
          type: c.type || c.Type || ''
        }));
      } else {
        const lines = content.split('\n').map(l => l.trim()).filter(Boolean);
        parsed = lines.map((l, i) => {
          let name = l;
          let park = '';
          let type = '';
          
          // Match "Rank. Name (Type) — Park" or "Name (Type) - Park"
          const match = l.match(/^(?:\d+\.\s*)?(.+?)(?:\s*\((.*?)\))?\s*(?:[-—]\s*(.*))?$/);
          if (match && (match[2] || match[3])) {
            name = match[1].trim();
            type = match[2] ? match[2].trim() : '';
            park = match[3] ? match[3].trim() : '';
          } else if (l.includes('@')) {
            const parts = l.split('@');
            name = parts[0].trim();
            park = parts[1].trim();
          } else if (l.includes(',')) {
            const parts = l.split(',');
            name = parts[0].trim();
            park = parts[1].trim();
          } else {
            // Strip leading rank if it's just "1. Name"
            name = name.replace(/^\d+\.\s*/, '').trim();
          }
          return { id: i+1, name, park, type };
        });
      }
    }
    
    if (parsed.length < 2) {
      setError('Found fewer than 2 coasters. Make sure your list is formatted correctly.');
      return;
    }
    onReady(parsed);
  };

  const onDrop = useCallback(acceptedFiles => {
    const file = acceptedFiles[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      processText(reader.result);
    };
    reader.readAsText(file);
  }, [onReady]);

  const {getRootProps, getInputProps, isDragActive} = useDropzone({onDrop});

  return (
    <div className="cr-fadein">
      <h2 className="cr-display" style={{ fontSize: 32, letterSpacing: '0.05em', margin: '0 0 4px' }}>IMPORT COASTERS</h2>
      <p className="cr-text-soft" style={{ fontSize: 14, margin: '0 0 20px' }}>
        Paste a list of coasters, a CSV (with name, park columns), or JSON array.
      </p>
      
      <div {...getRootProps()} style={{
        border: '2px dashed var(--border)', padding: 30, textAlign: 'center', marginBottom: 20,
        backgroundColor: isDragActive ? 'var(--surface-hi)' : 'var(--surface)', cursor: 'pointer'
      }}>
        <input {...getInputProps()} />
        <Upload size={32} className="cr-text-amber" style={{marginBottom: 10}}/>
        <div className="cr-meta">Drag & Drop a file here, or click to select</div>
      </div>
      
      <div style={{textAlign: 'center', marginBottom: 20}} className="cr-meta">OR PASTE TEXT</div>
      
      <textarea 
        className="cr-input" 
        style={{height: 150, resize: 'vertical'}}
        placeholder="Name @ Park
Fury 325 @ Carowinds
Millennium Force @ Cedar Point"
        value={text}
        onChange={e => setText(e.target.value)}
      />
      {error && <div className="cr-text-red cr-meta" style={{marginBottom: 10}}>{error}</div>}
      <button className="cr-btn primary" onClick={() => processText(text)}>
        Import List
      </button>
    </div>
  )
}

function ModeSelector({ n, onStart, minK = 0 }) {
  const opts = [];
  opts.push({ label: 'FULL RANKING', desc: `Complete 1 → ${n} ordering.`, mode: 'full', k: 0 });
  for (const k of [50, 25, 10]) {
    if (k < n && k > minK) opts.push({ label: `TOP ${k}`, desc: `Find and rank just your top ${k}.`, mode: 'topK', k });
  }
  return (
    <div className="cr-fadein">
      <h2 className="cr-display" style={{ fontSize: 32, letterSpacing: '0.05em', margin: '0 0 4px' }}>SELECT MODE</h2>
      <p className="cr-text-soft" style={{ fontSize: 14, margin: '0 0 20px' }}>
        How precise do you want to be? Estimates assume ~4 seconds per pick.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
        {opts.map(o => {
          const cmps = estimateComparisons(n, o.mode, o.k);
          const mins = estimateMinutes(cmps);
          return (
            <button key={o.label + o.k} onClick={() => onStart(o.mode, o.k)} className="cr-mode-card">
              <div className="cr-display" style={{ fontSize: 28, letterSpacing: '0.04em', marginBottom: 4 }}>
                {o.label}
              </div>
              <div className="cr-text-soft" style={{ fontSize: 13, marginBottom: 14, lineHeight: 1.4 }}>
                {o.desc}
              </div>
              <div className="cr-meta cr-text-amber" style={{ fontSize: 11 }}>
                ~{cmps} picks · ~{mins} min
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function getManufacturerInfo(type) {
  if (!type) return { label: '—', bg: '#2a2218', fg: '#b8ab97' };
  if (type.includes('B&M')) return { label: 'B&M', bg: '#1e3a5c', fg: '#e8f0fa' };
  if (type.includes('RMC')) return { label: 'RMC', bg: '#a8281f', fg: '#fde8e6' };
  if (type.includes('Intamin')) return { label: 'INTAMIN', bg: '#d4a017', fg: '#1a1208' };
  if (type.includes('Vekoma')) return { label: 'VEKOMA', bg: '#c0511a', fg: '#fef0e6' };
  if (type.includes('Arrow')) return { label: 'ARROW', bg: '#5a5a5a', fg: '#f0f0f0' };
  if (type.includes('GCI')) return { label: 'GCI', bg: '#7a4a2a', fg: '#fce8d0' };
  if (type.includes('Schwarzkopf')) return { label: 'SCHWARZKOPF', bg: '#8a2a2a', fg: '#fde8e0' };
  if (type.includes('Wooden') || type.includes('Wood')) return { label: 'WOODEN', bg: '#7a4a2a', fg: '#fce8d0' };
  if (type.includes('Mack')) return { label: 'MACK', bg: '#2a7a4a', fg: '#e0fae8' };
  if (type.includes('Premier')) return { label: 'PREMIER', bg: '#6a3a7a', fg: '#f4e6fa' };
  if (type.includes('S&S')) return { label: 'S&S', bg: '#b08a17', fg: '#fff8e0' };
  if (type.includes('Gerstlauer')) return { label: 'GERSTLAUER', bg: '#2a5a7a', fg: '#e0eefa' };
  if (type.includes('Giovanola')) return { label: 'GIOVANOLA', bg: '#5a6a7a', fg: '#eaf0f5' };
  if (type.includes('Morgan')) return { label: 'MORGAN', bg: '#7a7a3a', fg: '#fafae0' };
  if (type.includes('Zamperla')) return { label: 'ZAMPERLA', bg: '#2a7a7a', fg: '#e0fafa' };
  if (type.includes('Zierer')) return { label: 'ZIERER', bg: '#5a7a3a', fg: '#eafae0' };
  if (type.includes('Reverchon')) return { label: 'REVERCHON', bg: '#7a5a3a', fg: '#fceadc' };
  if (type.includes('Maurer')) return { label: 'MAURER', bg: '#8a7a3a', fg: '#fdf6dc' };
  if (type.includes('Wiegand')) return { label: 'WIEGAND', bg: '#6a6a8a', fg: '#eaeafa' };
  // Classic wooden-coaster builders — without these they fell through to the
  // blank "—" tile, which is what The Racer, GhostRider and The Boss were showing.
  if (type.includes('Philadelphia Toboggan')) return { label: 'PTC', bg: '#6a4327', fg: '#fce8d0' };
  if (type.includes('Custom Coasters')) return { label: 'CCI', bg: '#8a5a2a', fg: '#fce8d0' };
  if (type.includes('Dinn')) return { label: 'DINN', bg: '#6a5030', fg: '#fce8d0' };
  if (type.includes('Gravitykraft')) return { label: 'GRAVITYKRAFT', bg: '#7a6236', fg: '#fdf2dc' };
  if (type.includes('Opus')) return { label: 'OPUS', bg: '#5a4a30', fg: '#f5ead6' };
  if (type.includes('Setpoint')) return { label: 'SETPOINT', bg: '#3a5a6a', fg: '#e0f0fa' };
  if (type.includes('Great Coasters')) return { label: 'GCII', bg: '#7a4a2a', fg: '#fce8d0' };
  if (type.includes('Chance')) return { label: 'CHANCE', bg: '#4a5a7a', fg: '#e6eefa' };
  if (type.includes('Indoor')) return { label: 'INDOOR', bg: '#3a3550', fg: '#e0dcfa' };
  if (type.includes('Family')) return { label: 'FAMILY', bg: '#5a7a8a', fg: '#e0f0fa' };
  return { label: '—', bg: '#2a2218', fg: '#b8ab97' };
}

const noopUpdate = () => {};

function CoasterImage({ coaster, onUpdate }) {
  const [failed, setFailed] = useState(false);
  
  useEffect(() => {
    if (!coaster.image && !coaster.fetched) {
      // mark as fetched so we don't spam
      onUpdate(coaster.id, { fetched: true });
      fetch('/api/fetch_coaster', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(coaster)
      })
      .then(r => r.json())
      .then(data => {
         if (data.image || data.type) {
           onUpdate(coaster.id, { 
             image: data.image || null,
             type: data.type || coaster.type
           });
         }
      })
      .catch(() => {});
    }
  }, [coaster, onUpdate]);

  if (!coaster.image || failed) {
    const m = getManufacturerInfo(coaster.type);
    return (
      <div className="cr-img-wrap cr-chip" style={{ background: m.bg }}>
        <div className="cr-chip-label" style={{ color: m.fg }}>{m.label}</div>
        <div className="cr-chip-sub" style={{ color: m.fg, opacity: 0.7 }}>{coaster.type}</div>
      </div>
    );
  }

  return (
    <div className="cr-img-wrap">
      <img
        src={coaster.image}
        alt={coaster.name}
        className="cr-img"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

function CompareView({ left, right, onChoose, keyPulse, onUpdateCoaster }) {
  const [fixTarget, setFixTarget] = useState(null);

  return (
    <div className="cr-fadein" key={keyPulse}>
      <div className="cr-meta" style={{ textAlign: 'center', marginBottom: 16, letterSpacing: '0.3em' }}>
        Which ride wins?
      </div>

      <div className="cr-pair">
        <CoasterCardFull coaster={left} side="L" onClick={() => onChoose('l')} onFix={() => setFixTarget(left)} onUpdate={onUpdateCoaster} />
        <div className="cr-vs">vs</div>
        <CoasterCardFull coaster={right} side="R" onClick={() => onChoose('r')} onFix={() => setFixTarget(right)} onUpdate={onUpdateCoaster} />
      </div>

      <style>{`
        .cr-pair {
          display: grid; grid-template-columns: 1fr; gap: 6px;
          align-items: stretch;
        }
        @media (min-width: 900px) {
          .cr-pair { grid-template-columns: 1fr auto 1fr; gap: 20px; }
        }
        .cr-fix-btn {
          position: absolute; bottom: 8px; right: 8px;
          opacity: 0; transition: opacity 200ms;
          background: var(--surface-active); border: 1px solid var(--amber);
          color: var(--amber); padding: 4px 8px; border-radius: 4px;
          font-size: 10px; z-index: 10; display: flex; align-items: center; gap: 4px;
        }
        .cr-card:hover .cr-fix-btn { opacity: 1; }
      `}</style>
      
      {fixTarget && (
        <FixDialog 
          coaster={fixTarget} 
          onClose={() => setFixTarget(null)}
          onSave={(updates) => {
            onUpdateCoaster(fixTarget.id, updates);
            setFixTarget(null);
          }}
        />
      )}
    </div>
  );
}

function FixDialog({ coaster, onClose, onSave }) {
  const [type, setType] = useState(coaster.type || '');
  const [image, setImage] = useState('');
  
  return (
    <div className="cr-modal-bg" onClick={onClose} style={{zIndex: 9999}}>
      <div className="cr-modal" onClick={e => e.stopPropagation()}>
        <h3 className="cr-display" style={{ fontSize: 22, letterSpacing: '0.05em', margin: '0 0 12px' }}>FIX COASTER</h3>
        <p className="cr-text-soft" style={{ fontSize: 12, margin: '0 0 20px', lineHeight: 1.5 }}>
          Description incorrect? Image missing? Update it here. You can paste an image URL to replace the missing or incorrect image.
        </p>
        
        <div className="cr-meta" style={{marginBottom: 4}}>Type / Manufacturer</div>
        <input className="cr-input" value={type} onChange={e => setType(e.target.value)} placeholder="e.g. B&M Hyper" />
        
        <div className="cr-meta" style={{marginBottom: 4}}>Image URL (optional)</div>
        <input className="cr-input" value={image} onChange={e => setImage(e.target.value)} placeholder="https://..." />
        
        <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
          <button onClick={onClose} className="cr-btn" style={{ flex: 1, justifyContent: 'center' }}>Cancel</button>
          <button onClick={() => onSave({type, image: image || coaster.image})} className="cr-btn primary" style={{ flex: 1, justifyContent: 'center' }}>Save</button>
        </div>
      </div>
    </div>
  );
}

function CoasterCardFull({ coaster, side, onClick, onFix, onUpdate }) {
  return (
    <div style={{position: 'relative', display: 'flex'}}>
      <button className="cr-card" onClick={onClick}>
        <CoasterImage coaster={coaster} onUpdate={onUpdate} />
        <div className="cr-card-body">
          <span className={`cr-card-marker ${side === 'L' ? 'left' : 'right'}`}>
            {side === 'L' ? '← 01' : '02 →'}
          </span>
          <div className="cr-card-name">{(coaster.name || '').toUpperCase()}</div>
          <div className="cr-card-type">{coaster.type}</div>
          <div className="cr-card-park">{coaster.park}</div>
        </div>
        <div className="cr-fix-btn" onClick={(e) => { e.stopPropagation(); onFix(); }}>
          <Edit3 size={10} /> Fix Info
        </div>
      </button>
    </div>
  );
}

function StandingsView({ state, onBack, coasters }) {
  if (state.mode === 'topK') {
    const remaining = (state.pending ? state.pending.length : 0) + (state.ev ? 1 : 0);
    return (
      <div className="cr-fadein">
        <button onClick={onBack} className="cr-btn" style={{ marginBottom: 18 }}>
          <ArrowLeft size={12} /> Back to comparisons
        </button>
        <h2 className="cr-headline" style={{ margin: '0 0 8px' }}>CURRENT LEADERBOARD</h2>
        <p className="cr-text-soft" style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 22, maxWidth: 600 }}>
          These are the {state.topK.length} coasters that have survived so far, in current order.
          {remaining > 0 ? ` ${remaining} more to evaluate.` : ''} The bottom of this list can still be displaced.
        </p>
        <ol style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          {state.topK.map((c, i) => (
            <li key={c.id} className="cr-rank-row">
              <span className="cr-rank-num">{String(i + 1).padStart(2, '0')}</span>
              <span className="cr-rank-name">{c.name}</span>
              <span className="cr-rank-type">{c.type}</span>
              <span className="cr-rank-park">{c.park}</span>
            </li>
          ))}
        </ol>
      </div>
    );
  }

  const runs = [...state.queue];
  if (state.currentMerge) {
    const partial = [
      ...state.currentMerge.result,
      ...state.currentMerge.left.slice(state.currentMerge.leftIdx),
      ...state.currentMerge.right.slice(state.currentMerge.rightIdx),
    ];
    runs.push(partial);
  }
  runs.sort((a, b) => b.length - a.length);
  const longest = runs[0] || [];

  return (
    <div className="cr-fadein">
      <button onClick={onBack} className="cr-btn" style={{ marginBottom: 18 }}>
        <ArrowLeft size={12} /> Back to comparisons
      </button>
      <h2 className="cr-headline" style={{ margin: '0 0 8px' }}>PARTIAL STANDINGS</h2>
      <p className="cr-text-soft" style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 22, maxWidth: 600 }}>
        Mid-sort, your ranking exists as several sorted groups. The longest is your best partial estimate.
      </p>
      <div style={{ marginBottom: 28 }}>
        <div className="cr-meta" style={{ marginBottom: 12 }}>
          Longest sorted group · {longest.length} of {coasters.length}
        </div>
        <ol style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          {longest.map((c, i) => (
            <li key={c.id} className="cr-rank-row">
              <span className="cr-rank-num">{String(i + 1).padStart(2, '0')}</span>
              <span className="cr-rank-name">{c.name}</span>
              <span className="cr-rank-type">{c.type}</span>
              <span className="cr-rank-park">{c.park}</span>
            </li>
          ))}
        </ol>
      </div>
      {runs.length > 1 && (
        <div>
          <div className="cr-meta" style={{ marginBottom: 10 }}>Other groups · {runs.length - 1}</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 8 }}>
            {runs.slice(1).map((r, i) => (
              <div key={i} className="cr-meta" style={{ border: '1px solid var(--border)', padding: '6px 10px', opacity: 0.6 }}>
                Group of {r.length}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function FinalView({ ranking, mode, k, totalN, count, onCopy, onCopyCSV, copied, onReset, onUndo, onRankMore }) {
  const [tab, setTab] = useState('full');
  if (!ranking || ranking.length === 0) {
    return (
      <div className="cr-fadein">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
          <Trophy size={28} className="cr-text-amber" />
          <h2 className="cr-display" style={{ fontSize: 38, letterSpacing: '0.05em', margin: 0 }}>NOTHING TO RANK</h2>
        </div>
        <p className="cr-text-soft" style={{ fontSize: 14, marginBottom: 24, maxWidth: 560 }}>
          The COASTERS list is empty. Add entries and reload.
        </p>
        <button onClick={onReset} className="cr-btn danger">
          <RotateCcw size={14} /> Reset
        </button>
      </div>
    );
  }
  const podium = [0, 1, 2].filter(i => ranking[i]);
  const isTopK = mode === 'topK';
  const filteredOut = isTopK ? Math.max(0, (totalN || 0) - ranking.length) : 0;
  const heading = isTopK ? `TOP ${ranking.length}` : 'FINAL RANKING';
  
  return (
    <div className="cr-fadein">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <Trophy size={28} className="cr-text-amber" />
        <h2 className="cr-display" style={{ fontSize: 38, letterSpacing: '0.05em', margin: 0 }}>{heading}</h2>
      </div>
      <p className="cr-meta" style={{ marginBottom: filteredOut ? 6 : 24 }}>
        {count} comparisons · {ranking.length} coaster{ranking.length === 1 ? '' : 's'} ranked
      </p>
      {filteredOut > 0 && (
        <p className="cr-text-soft" style={{ fontSize: 13, marginBottom: 24 }}>
          {filteredOut} other coaster{filteredOut === 1 ? '' : 's'} didn't make the top {ranking.length}.
        </p>
      )}

      {ranking.length > 10 && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 24, flexWrap: 'wrap' }}>
          <button onClick={() => setTab('full')} className={`cr-btn ${tab === 'full' ? 'primary' : ''}`}>Full List</button>
          {ranking.length >= 50 && <button onClick={() => setTab('50')} className={`cr-btn ${tab === '50' ? 'primary' : ''}`}>Top 50</button>}
          {ranking.length >= 25 && <button onClick={() => setTab('25')} className={`cr-btn ${tab === '25' ? 'primary' : ''}`}>Top 25</button>}
          {ranking.length >= 10 && <button onClick={() => setTab('10')} className={`cr-btn ${tab === '10' ? 'primary' : ''}`}>Top 10</button>}
        </div>
      )}

      {(() => {
        let displayRank = ranking;
        if (tab === '50') displayRank = ranking.slice(0, 50);
        else if (tab === '25') displayRank = ranking.slice(0, 25);
        else if (tab === '10') displayRank = ranking.slice(0, 10);
        const podium = [0, 1, 2].filter(i => displayRank[i]);

        return (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 10, marginBottom: 32 }}>
              {podium.map(i => {
                const c = displayRank[i];
                const cls = i === 0 ? 'gold' : i === 1 ? 'silver' : 'bronze';
                const num = i === 0 ? 'cr-text-amber' : i === 1 ? 'cr-text-silver' : 'cr-text-bronze';
                return (
                  <div key={c.id} className={`cr-podium ${cls}`}>
                    <CoasterImage coaster={c} onUpdate={noopUpdate} />
                    <div className="cr-podium-body">
                <div className={`cr-display ${num}`} style={{ fontSize: 48, letterSpacing: '0.05em', lineHeight: 1, marginBottom: 6 }}>
                  {String(i + 1).padStart(2, '0')}
                </div>
                <div className="cr-display" style={{ fontSize: 22, lineHeight: 1.05, marginBottom: 6, letterSpacing: '0.02em' }}>
                  {c.name}
                </div>
                <div className="cr-meta cr-text-amber" style={{ marginBottom: 4 }}>{c.type}</div>
                <div className="cr-meta">{c.park}</div>
              </div>
            </div>
          );
        })}
      </div>

      <ol style={{ margin: 0, padding: 0, listStyle: 'none', marginBottom: 24 }}>
        {displayRank.slice(3).map((c, i) => (
          <li key={c.id} className="cr-rank-row">
            <span className="cr-rank-num">{String(i + 4).padStart(3, '0')}</span>
            <span className="cr-rank-name">{c.name}</span>
            <span className="cr-rank-type">{c.type}</span>
            <span className="cr-rank-park">{c.park}</span>
          </li>
        ))}
      </ol>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <button onClick={onCopy} className="cr-btn primary">
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? 'Copied' : 'Copy text'}
          </button>
          <button onClick={onCopyCSV} className="cr-btn">
            <Copy size={14} /> Copy CSV
          </button>
          <button onClick={onUndo} className="cr-btn">
            <Undo2 size={14} /> Undo last
          </button>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          {onRankMore && mode === 'topK' && (
            <button onClick={onRankMore} className="cr-btn primary">
              <ListOrdered size={14} /> Rank more
            </button>
          )}
          <button onClick={onReset} className="cr-btn danger">
            <RotateCcw size={14} /> Start over
          </button>
        </div>
      </div>
          </>
        );
      })()}
    </div>
  );
}

function formatRanking(ranking, mode, k) {
  const header = mode === 'topK' ? `TOP ${ranking.length}` : `FULL RANKING (1–${ranking.length})`;
  const body = ranking.map((c, i) => `${i + 1}. ${c.name} (${c.type}) — ${c.park}`).join('\n');
  return `${header}\n${body}`;
}

function formatCSV(ranking, mode, k) {
  const lines = ['Rank,Coaster,Type,Park'];
  ranking.forEach((c, i) => {
    const q = s => (typeof s === 'string' && (s.includes(',') || s.includes('"'))) ? `"${s.replace(/"/g, '""')}"` : s;
    lines.push(`${i + 1},${q(c.name)},${q(c.type)},${q(c.park)}`);
  });
  return lines.join('\n');
}

function copyToClipboard(text) {
  if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  else fallbackCopy(text);
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (e) {}
  document.body.removeChild(ta);
}
