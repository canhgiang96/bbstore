(() => {
  "use strict";

  const DB_NAME = "bbstore_dashboard";
  const DB_VERSION = 1;

  // IndexedDB keyPaths must be identifier-like (no spaces), so every store uses a
  // plain out-of-line autoIncrement key. `matchField` (when set) names the column
  // that should behave like a natural unique key: bulkPut looks up existing rows by
  // that column's value and updates them in place instead of appending duplicates.
  const STORE_DEFS = {
    master: { matchField: "SKU phân loại" },
    combo: { matchField: "SKU COMBO" },
    cashflow: { matchField: "Mã đơn hàng" },
    adjustments: { matchField: null },
  };

  let dbPromise = null;

  function openDB() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const name of Object.keys(STORE_DEFS)) {
          if (db.objectStoreNames.contains(name)) continue;
          db.createObjectStore(name, { autoIncrement: true });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return dbPromise;
  }

  function withStore(storeName, mode, fn) {
    return openDB().then(db => new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, mode);
      const store = tx.objectStore(storeName);
      const result = fn(store, resolve, reject);
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    }));
  }

  // Returns [{ key, value }] — key is the true IDB primary key.
  function getAllWithKeys(storeName) {
    return openDB().then(db => new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, "readonly");
      const store = tx.objectStore(storeName);
      const out = [];
      const req = store.openCursor();
      req.onsuccess = e => {
        const cursor = e.target.result;
        if (cursor) {
          out.push({ key: cursor.primaryKey, value: cursor.value });
          cursor.continue();
        } else {
          resolve(out);
        }
      };
      req.onerror = () => reject(req.error);
    }));
  }

  function count(storeName) {
    return withStore(storeName, "readonly", store => new Promise((resolve, reject) => {
      const req = store.count();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }));
  }

  // Insert (key omitted) or update (key given) a single record.
  function put(storeName, value, key) {
    return withStore(storeName, "readwrite", store => new Promise((resolve, reject) => {
      const req = key === undefined ? store.put(value) : store.put(value, key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }));
  }

  // Bulk import raw rows parsed from an uploaded file. If the store defines a
  // matchField, a row whose matchField value equals an existing record's is
  // written back to that same key (upsert); everything else is appended.
  // Returns the number of rows written.
  function bulkPut(storeName, rows) {
    const def = STORE_DEFS[storeName];
    return openDB().then(db => new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, "readwrite");
      const store = tx.objectStore(storeName);

      function writeRows(matchMap) {
        rows.forEach(row => {
          const r = { ...row };
          const matchVal = def.matchField ? r[def.matchField] : null;
          if (matchVal) {
            const existingKey = matchMap.get(matchVal);
            const req = store.put(r, existingKey);
            req.onsuccess = () => matchMap.set(matchVal, req.result);
          } else {
            store.put(r);
          }
        });
      }

      if (def.matchField) {
        const matchMap = new Map();
        const cursorReq = store.openCursor();
        cursorReq.onsuccess = e => {
          const cursor = e.target.result;
          if (cursor) {
            const v = cursor.value[def.matchField];
            if (v) matchMap.set(v, cursor.primaryKey);
            cursor.continue();
          } else {
            writeRows(matchMap);
          }
        };
        cursorReq.onerror = () => reject(cursorReq.error);
      } else {
        writeRows(new Map());
      }

      tx.oncomplete = () => resolve(rows.length);
      tx.onerror = () => reject(tx.error);
    }));
  }

  function remove(storeName, key) {
    return withStore(storeName, "readwrite", store => store.delete(key));
  }

  function clear(storeName) {
    return withStore(storeName, "readwrite", store => store.clear());
  }

  window.DB = { STORE_DEFS, getAllWithKeys, put, bulkPut, delete: remove, clear, countAsync: count };
})();
