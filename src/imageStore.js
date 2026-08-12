// Image references live here rather than in localStorage. The backend now serves
// images as URLs, but older saved lists may still hold inline base64 data URLs
// (~200-500KB each), which would blow straight past the ~5MB localStorage quota.
// IndexedDB has room for either.

const DB_NAME = 'coaster-ranker';
const STORE = 'images';

let dbPromise = null;

function openDB() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise(resolve => {
    if (typeof indexedDB === 'undefined') return resolve(null);
    let req;
    try {
      req = indexedDB.open(DB_NAME, 1);
    } catch (e) {
      return resolve(null);
    }
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
    req.onblocked = () => resolve(null);
  });
  return dbPromise;
}

// What's already on disk, so importing a list doesn't rewrite every megabyte
// each time another chunk of coasters comes back from the backend.
const written = new Map();

// Every helper resolves rather than rejects: a browser with IndexedDB disabled
// should degrade to placeholder chips, never take the app down.
export async function saveImages(coasters) {
  const changed = coasters.filter(c => written.get(String(c.id)) !== (c.image || null));
  if (changed.length === 0) return;

  const db = await openDB();
  if (!db) return;
  await new Promise(resolve => {
    let tx;
    try {
      tx = db.transaction(STORE, 'readwrite');
    } catch (e) {
      return resolve();
    }
    const store = tx.objectStore(STORE);
    for (const c of changed) {
      const key = String(c.id);
      try {
        if (c.image) store.put(c.image, key);
        else store.delete(key);
        written.set(key, c.image || null);
      } catch (e) {}
    }
    tx.oncomplete = resolve;
    tx.onerror = resolve;
    tx.onabort = resolve;
  });
}

export async function loadImages() {
  const db = await openDB();
  const map = new Map();
  if (!db) return map;
  await new Promise(resolve => {
    let req;
    try {
      req = db.transaction(STORE, 'readonly').objectStore(STORE).openCursor();
    } catch (e) {
      return resolve();
    }
    req.onsuccess = () => {
      const cursor = req.result;
      if (!cursor) return resolve();
      map.set(String(cursor.key), cursor.value);
      // Already on disk — don't write it straight back on the next persist.
      written.set(String(cursor.key), cursor.value);
      cursor.continue();
    };
    req.onerror = () => resolve();
  });
  return map;
}

export async function clearImages() {
  written.clear();
  const db = await openDB();
  if (!db) return;
  await new Promise(resolve => {
    let tx;
    try {
      tx = db.transaction(STORE, 'readwrite');
    } catch (e) {
      return resolve();
    }
    try { tx.objectStore(STORE).clear(); } catch (e) {}
    tx.oncomplete = resolve;
    tx.onerror = resolve;
    tx.onabort = resolve;
  });
}
