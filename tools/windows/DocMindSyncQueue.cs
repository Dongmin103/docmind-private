using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace DocMind
{
    public sealed class SyncEntry
    {
        public string SourceId, RelativePath, PathKey, Kind, OldRelativePath, Fingerprint;
        public long Generation, LastChangedAt, DueAt;
        public int StableCount;
        public bool InitialRegistration;
    }

    public sealed class SyncOutbox
    {
        public string SourceId, RequestId, Payload;
        public long Epoch, Sequence, NextAttemptAt;
        public int Attempts;
        public bool Held;
    }

    public sealed class SyncScope
    {
        public string SourceId, RelativePath, OldRelativePath, Kind;
        public long Generation;
        public long DueAt;
        public int AbsenceCount;
    }

    // The host owns this file outside watched roots. All values are bound, and
    // one connection serializes event capture against freezing/acknowledgement.
    public sealed class SyncQueue : IDisposable
    {
        private IntPtr db;
        private readonly object gate = new object();
        private bool folderTablesReady;
        private static readonly IntPtr Transient = new IntPtr(-1);
        private const int Row = 100, Done = 101;

        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_open_v2(byte[] filename, out IntPtr handle, int flags, IntPtr vfs);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_close_v2(IntPtr handle);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_busy_timeout(IntPtr handle, int ms);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_prepare_v2(IntPtr handle, byte[] sql, int length, out IntPtr statement, IntPtr tail);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_bind_text(IntPtr statement, int index, byte[] value, int length, IntPtr destructor);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_bind_int64(IntPtr statement, int index, long value);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_bind_null(IntPtr statement, int index);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_step(IntPtr statement);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_finalize(IntPtr statement);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_column_count(IntPtr statement);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_column_type(IntPtr statement, int column);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern long sqlite3_column_int64(IntPtr statement, int column);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern IntPtr sqlite3_column_text(IntPtr statement, int column);
        [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
        private static extern int sqlite3_column_bytes(IntPtr statement, int column);

        private static byte[] Utf8(string text) { return Encoding.UTF8.GetBytes(text + "\0"); }
        private static void Check(int code)
        {
            if (code != 0) throw new IOException("SQLite operation failed (code " + code + ").");
        }

        private List<object[]> Query(string sql, params object[] values)
        {
            if (db == IntPtr.Zero) throw new ObjectDisposedException("SyncQueue");
            IntPtr statement;
            Check(sqlite3_prepare_v2(db, Utf8(sql), -1, out statement, IntPtr.Zero));
            try
            {
                for (int i = 0; i < values.Length; i++)
                {
                    object value = values[i];
                    if (value == null) Check(sqlite3_bind_null(statement, i + 1));
                    else if (value is string) Check(sqlite3_bind_text(statement, i + 1, Utf8((string)value), -1, Transient));
                    else Check(sqlite3_bind_int64(statement, i + 1, Convert.ToInt64(value)));
                }
                List<object[]> rows = new List<object[]>();
                int result;
                while ((result = sqlite3_step(statement)) == Row)
                {
                    object[] row = new object[sqlite3_column_count(statement)];
                    for (int i = 0; i < row.Length; i++)
                    {
                        int type = sqlite3_column_type(statement, i);
                        if (type == 5) row[i] = null;
                        else if (type == 1) row[i] = sqlite3_column_int64(statement, i);
                        else
                        {
                            byte[] bytes = new byte[sqlite3_column_bytes(statement, i)];
                            Marshal.Copy(sqlite3_column_text(statement, i), bytes, 0, bytes.Length);
                            row[i] = Encoding.UTF8.GetString(bytes);
                        }
                    }
                    rows.Add(row);
                }
                if (result != Done) Check(result);
                return rows;
            }
            finally { sqlite3_finalize(statement); }
        }

        private void Transaction(Action action)
        {
            Query("BEGIN IMMEDIATE");
            try { action(); Query("COMMIT"); }
            catch { Query("ROLLBACK"); throw; }
        }

        public SyncQueue(string databasePath)
        {
            string fullPath = Path.GetFullPath(databasePath);
            Directory.CreateDirectory(Path.GetDirectoryName(fullPath));
            int code = sqlite3_open_v2(Utf8(fullPath), out db, 2 | 4 | 0x10000, IntPtr.Zero);
            if (code != 0) { if (db != IntPtr.Zero) sqlite3_close_v2(db); db = IntPtr.Zero; Check(code); }
            try
            {
                Check(sqlite3_busy_timeout(db, 5000));
                Query("PRAGMA journal_mode=WAL");
                Query("PRAGMA synchronous=FULL");
                Query("CREATE TABLE IF NOT EXISTS counter(id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL)");
                Query("INSERT OR IGNORE INTO counter VALUES(1,0)");
                Query("CREATE TABLE IF NOT EXISTS pending(source TEXT NOT NULL,path_key TEXT NOT NULL,path TEXT NOT NULL,kind TEXT NOT NULL,old_path TEXT,generation INTEGER NOT NULL,changed_at INTEGER NOT NULL,due_at INTEGER NOT NULL,stable_count INTEGER NOT NULL DEFAULT 0,dirty_sent INTEGER NOT NULL DEFAULT 0,state INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(source,path_key))");
                bool hasFingerprint = false;
                bool hasInitialRegistration = false;
                foreach (object[] column in Query("PRAGMA table_info(pending)"))
                {
                    if ((string)column[1] == "fingerprint") hasFingerprint = true;
                    if ((string)column[1] == "initial_registration") hasInitialRegistration = true;
                }
                if (!hasFingerprint) Query("ALTER TABLE pending ADD COLUMN fingerprint TEXT");
                if (!hasInitialRegistration) Query("ALTER TABLE pending ADD COLUMN initial_registration INTEGER NOT NULL DEFAULT 0");
                bool hasIdentityCheck=false;
                bool hasIdentityFailure=false;
                foreach(object[] column in Query("PRAGMA table_info(pending)"))
                {
                    if((string)column[1]=="identity_checked_generation")hasIdentityCheck=true;
                    if((string)column[1]=="identity_failed_generation")hasIdentityFailure=true;
                }
                if(!hasIdentityCheck)Query("ALTER TABLE pending ADD COLUMN identity_checked_generation INTEGER NOT NULL DEFAULT 0");
                if(!hasIdentityFailure)Query("ALTER TABLE pending ADD COLUMN identity_failed_generation INTEGER NOT NULL DEFAULT 0");
                Query("CREATE INDEX IF NOT EXISTS pending_due ON pending(source,state,due_at)");
                Query("CREATE INDEX IF NOT EXISTS pending_dirty ON pending(source,state,dirty_sent)");
                Query("CREATE TABLE IF NOT EXISTS outbox(source TEXT PRIMARY KEY,epoch INTEGER NOT NULL,sequence INTEGER NOT NULL,request_id TEXT NOT NULL,payload TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at INTEGER NOT NULL DEFAULT 0,held INTEGER NOT NULL DEFAULT 0)");
                Query("CREATE TABLE IF NOT EXISTS outbox_item(source TEXT NOT NULL,ordinal INTEGER NOT NULL,path_key TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(source,ordinal))");
                Query("CREATE TABLE IF NOT EXISTS scope(source TEXT NOT NULL,path_key TEXT NOT NULL,path TEXT NOT NULL,old_path TEXT,kind TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(source,path_key))");
                FolderTables();
            }
            catch { Dispose(); throw; }
        }

        public static string NormalizePath(string path)
        {
            if (path == null) throw new ArgumentException("Relative path required.");
            path = path.Trim().Replace('\\', '/').Normalize(NormalizationForm.FormC);
            if (path.Length == 0 || path.Length > 1024 || path[0] == '/' || path.IndexOf(':') >= 0 || path.IndexOf('\0') >= 0)
                throw new ArgumentException("Invalid relative path.");
            foreach (string part in path.Split('/'))
                if (part.Length == 0 || part == "." || part == "..") throw new ArgumentException("Invalid relative path.");
            return path;
        }

        public long EnqueueDiscovered(string source, string path, string oldPath, long now)
        {
            return EnqueueCore(source, path, String.IsNullOrEmpty(oldPath) ? "create" : "move", oldPath, now, true);
        }

        public long Enqueue(string source, string path, string kind, string oldPath, long now)
        {
            return EnqueueCore(source, path, kind, oldPath, now, false);
        }

        // Enumeration is an observation, not a newer filesystem event. Never
        // reset a captured modification, deletion, move or in-flight hash.
        public long EnqueueScan(string source, string path, long now)
        {
            return EnqueueCore(source, path, "create", null, now, true);
        }

        private long EnqueueCore(string source, string path, string kind, string oldPath, long now, bool scan)
        {
            if (String.IsNullOrWhiteSpace(source)) throw new ArgumentException("Source required.");
            if (kind != "create" && kind != "upsert" && kind != "delete" && kind != "move") throw new ArgumentException("Invalid change kind.");
            path = NormalizePath(path);
            // PowerShell marshals a null string argument as String.Empty.
            oldPath = String.IsNullOrEmpty(oldPath) ? null : NormalizePath(oldPath);
            string key = UnicodeCaseFold.Fold(path);
            lock (gate)
            {
                long generation = 0;
                Transaction(delegate
                {
                    if (scan)
                    {
                        List<object[]> pending = Query("SELECT generation,state,kind,old_path FROM pending WHERE source=? AND path_key=?", source, key);
                        if (pending.Count != 0)
                        {
                            generation = (long)pending[0][0];
                            if (oldPath != null && (string)pending[0][2] != "delete" && pending[0][3]==null) {
                                if(Query("SELECT 1 FROM outbox_item WHERE source=? AND path_key=? AND generation=?",source,key,generation).Count!=0){
                                    Query("UPDATE counter SET generation=generation+1 WHERE id=1");generation=(long)Query("SELECT generation FROM counter WHERE id=1")[0][0];
                                }
                                Query("UPDATE pending SET old_path=?,kind='move',generation=? WHERE source=? AND path_key=? AND old_path IS NULL", oldPath,generation, source, key);
                                Query("INSERT OR REPLACE INTO move_hint(source,old_key,path_key,generation) VALUES(?,?,?,?)",source,UnicodeCaseFold.Fold(oldPath),key,generation);
                            }
                            // A previously acknowledged server deferral may be
                            // recoverable after configuration changes. Preserve
                            // event identity and evidence, but allow a fresh try.
                            if ((long)pending[0][1] == 1)
                                Query("UPDATE pending SET state=0,dirty_sent=0,due_at=MAX(due_at,?) WHERE source=? AND path_key=? AND NOT EXISTS(SELECT 1 FROM outbox_item WHERE source=? AND path_key=?)", now, source, key, source, key);
                            return;
                        }
                    }
                    Query("UPDATE counter SET generation=generation+1 WHERE id=1");
                    generation = (long)Query("SELECT generation FROM counter WHERE id=1")[0][0];
                    // Preserve the earliest old path across autosave notifications.
                    List<object[]> previous = Query("SELECT old_path,initial_registration FROM pending WHERE source=? AND path_key=?", source, key);
                    if (oldPath == null && previous.Count != 0) oldPath = (string)previous[0][0];
                    bool initialRegistration = kind == "create" && previous.Count == 0;
                    if (previous.Count != 0 && kind != "delete") initialRegistration = (long)previous[0][1] != 0;
                    if (kind == "move" && oldPath != null)
                    {
                        string oldKey = UnicodeCaseFold.Fold(oldPath);
                        List<object[]> old = Query("SELECT old_path FROM pending WHERE source=? AND path_key=?", source, oldKey);
                        if (!scan && old.Count != 0 && old[0][0] != null) oldPath = (string)old[0][0];
                        if (oldKey != key) Query("DELETE FROM pending WHERE source=? AND path_key=?", source, oldKey);
                    }
                    if (oldPath != null && kind == "upsert") kind = "move";
                    if (kind == "create") kind = "upsert";
                    Query("INSERT OR REPLACE INTO pending(source,path_key,path,kind,old_path,generation,changed_at,due_at,initial_registration) VALUES(?,?,?,?,?,?,?,?,?)",
                        source, key, path, kind, oldPath, generation, now, kind == "delete" || initialRegistration ? now : checked(now + 120000), initialRegistration ? 1 : 0);
                    if(kind=="move" && oldPath!=null)
                        Query("INSERT OR REPLACE INTO move_hint(source,old_key,path_key,generation) VALUES(?,?,?,?)",source,UnicodeCaseFold.Fold(oldPath),key,generation);
                });
                return generation;
            }
        }

        private static string MoveProtection()
        {
            string protection = " AND NOT(kind='delete' AND EXISTS(SELECT 1 FROM move_hint h JOIN pending m ON m.source=h.source AND m.path_key=h.path_key AND m.generation=h.generation AND m.kind='move' WHERE h.source=pending.source AND h.old_key=pending.path_key))";
            // Cloud providers may emit create + delete instead of rename. Do
            // not destroy the registered origin before metadata probes finish.
            protection += " AND NOT(kind='delete' AND EXISTS(SELECT 1 FROM pending p WHERE p.source=pending.source AND p.kind<>'delete' AND p.initial_registration=1 AND p.old_path IS NULL AND p.identity_checked_generation<p.generation AND p.identity_failed_generation<>p.generation))";
            protection += " AND NOT(old_path IS NULL AND kind<>'delete' AND EXISTS(SELECT 1 FROM folder_scope f WHERE f.source=pending.source AND f.kind='move' AND (pending.path_key=f.path_key OR substr(pending.path_key,1,length(f.path_key)+1)=f.path_key||'/')))";
            protection += " AND NOT(kind='delete' AND EXISTS(SELECT 1 FROM folder_move_origin o JOIN folder_scope f ON f.source=o.source AND f.path_key=o.path_key AND f.kind='move' WHERE o.source=pending.source AND (pending.path_key=o.origin_key OR substr(pending.path_key,1,length(o.origin_key)+1)=o.origin_key||'/')))";
            return protection;
        }

        private SyncEntry[] Entries(string source, long now, int limit, bool dirty)
        {
            if (limit < 1 || limit > 250) throw new ArgumentOutOfRangeException("limit");
            string condition = dirty ? "dirty_sent=0" : "due_at<=?";
            string order = dirty ? "due_at,path_key" : "CASE WHEN stable_count>0 THEN 0 ELSE 1 END,due_at,path_key";
            string protection=MoveProtection();
            string sql = "SELECT source,path_key,path,kind,old_path,generation,changed_at,due_at,stable_count,fingerprint,initial_registration FROM pending WHERE source=? AND state=0 AND " + condition + protection + " AND NOT EXISTS(SELECT 1 FROM outbox WHERE source=?) ORDER BY " + order + " LIMIT ?";
            List<object[]> rows = dirty ? Query(sql, source, source, limit) : Query(sql, source, now, source, limit);
            List<SyncEntry> result = new List<SyncEntry>();
            foreach (object[] row in rows)
                result.Add(new SyncEntry { SourceId = (string)row[0], PathKey = (string)row[1], RelativePath = (string)row[2], Kind = (string)row[3], OldRelativePath = (string)row[4], Generation = (long)row[5], LastChangedAt = (long)row[6], DueAt = (long)row[7], StableCount = (int)(long)row[8], Fingerprint = (string)row[9], InitialRegistration = (long)row[10] != 0 });
            return result.ToArray();
        }

        public SyncEntry[] Due(string source, long now, int limit) { lock (gate) { return Entries(source, now, limit, false); } }
        public SyncEntry[] Dirty(string source, int limit) { lock (gate) { return Entries(source, 0, limit, true); } }
        public long Count(string source) { lock (gate) { return (long)Query("SELECT count(*) FROM pending WHERE source=?", source)[0][0]; } }

        public SyncEntry[] MoveIdentityProbes(string source,long now,int limit)
        {
            if(limit<1 || limit>250)throw new ArgumentOutOfRangeException("limit");
            lock(gate){var entries=new List<SyncEntry>();
                foreach(object[] row in Query("SELECT path_key,path,kind,old_path,generation,changed_at,due_at,stable_count,fingerprint FROM pending WHERE source=? AND kind<>'delete' AND initial_registration=1 AND old_path IS NULL AND identity_checked_generation<generation AND due_at<=? AND NOT EXISTS(SELECT 1 FROM outbox WHERE source=?) ORDER BY due_at,path_key LIMIT ?",source,now,source,limit))
                    entries.Add(new SyncEntry{SourceId=source,PathKey=(string)row[0],RelativePath=(string)row[1],Kind=(string)row[2],OldRelativePath=(string)row[3],Generation=(long)row[4],LastChangedAt=(long)row[5],DueAt=(long)row[6],StableCount=(int)(long)row[7],Fingerprint=(string)row[8],InitialRegistration=true});
                return entries.ToArray();}
        }

        public bool NeedsMoveIdentityProbe(SyncEntry entry)
        {lock(gate){return Query("SELECT 1 FROM pending WHERE source=? AND path_key=? AND generation=? AND kind<>'delete' AND initial_registration=1 AND old_path IS NULL AND identity_checked_generation<generation",entry.SourceId,entry.PathKey,entry.Generation).Count!=0;}}

        public void CompleteMoveIdentityProbe(SyncEntry entry)
        {lock(gate){Query("UPDATE pending SET identity_checked_generation=? WHERE source=? AND path_key=? AND generation=?",entry.Generation,entry.SourceId,entry.PathKey,entry.Generation);}}

        // A metadata access failure retains this file for retry; it must not
        // indefinitely prevent unrelated confirmed absences in the source.
        public void FailMoveIdentityProbe(SyncEntry entry,long now)
        {lock(gate){Query("UPDATE pending SET identity_failed_generation=?,due_at=? WHERE source=? AND path_key=? AND generation=?",entry.Generation,checked(now+5000),entry.SourceId,entry.PathKey,entry.Generation);}}

        public bool EnqueueAbsentMoveProbe(SyncEntry entry,long now)
        {
            lock(gate){bool changed=false;Transaction(delegate {
                if(Query("SELECT 1 FROM pending WHERE source=? AND path_key=? AND generation=?",entry.SourceId,entry.PathKey,entry.Generation).Count==0)return;
                Query("UPDATE counter SET generation=generation+1 WHERE id=1");
                long generation=(long)Query("SELECT generation FROM counter WHERE id=1")[0][0];
                Query("UPDATE pending SET kind='delete',old_path=NULL,generation=?,changed_at=?,due_at=?,stable_count=0,dirty_sent=0,state=0,fingerprint=NULL,initial_registration=0,identity_checked_generation=0,identity_failed_generation=0 WHERE source=? AND path_key=? AND generation=?",generation,now,now,entry.SourceId,entry.PathKey,entry.Generation);
                changed=true;
            });return changed;}
        }

        public void Delay(SyncEntry entry, long now, long milliseconds, int stableCount)
        {
            lock (gate)
                Query("UPDATE pending SET due_at=?,stable_count=? WHERE source=? AND path_key=? AND generation=?",
                    checked(now + milliseconds), stableCount, entry.SourceId, entry.PathKey, entry.Generation);
        }

        public bool RememberFingerprint(SyncEntry entry, string fingerprint)
        {
            if (fingerprint == null || fingerprint.Length != 64) throw new ArgumentException("Invalid fingerprint.");
            lock (gate)
            {
                Query("UPDATE pending SET fingerprint=? WHERE source=? AND path_key=? AND generation=?", fingerprint, entry.SourceId, entry.PathKey, entry.Generation);
                return (long)Query("SELECT count(*) FROM pending WHERE source=? AND path_key=? AND generation=? AND fingerprint=?", entry.SourceId, entry.PathKey, entry.Generation, fingerprint)[0][0] == 1;
            }
        }

        public void ResetForNewEpoch(string source, long now)
        {
            lock (gate) Transaction(delegate
            {
                Query("DELETE FROM outbox_item WHERE source=?", source);
                Query("DELETE FROM outbox WHERE source=?", source);
                Query("UPDATE pending SET dirty_sent=0,stable_count=0,fingerprint=NULL,due_at=CASE WHEN kind='delete' OR initial_registration=1 THEN ? WHEN changed_at+120000>? THEN changed_at+120000 ELSE ? END WHERE source=?", now, now, now, source);
            });
        }

        public bool Freeze(string source, long epoch, long sequence, string requestId, string payload, SyncEntry[] entries)
        {
            if (entries == null || entries.Length == 0 || entries.Length > 250 || Encoding.UTF8.GetByteCount(payload) > 1048576)
                throw new ArgumentException("Invalid outbox batch.");
            lock (gate)
            {
                bool frozen = false;
                Transaction(delegate
                {
                    if (Query("SELECT source FROM outbox WHERE source=?", source).Count != 0) return;
                    HashSet<string> seen = new HashSet<string>();
                    foreach (SyncEntry entry in entries)
                    {
                        if (entry.SourceId != source || !seen.Add(entry.PathKey)) throw new ArgumentException("Invalid outbox identity.");
                        List<object[]> current = Query("SELECT generation,kind,old_path FROM pending WHERE source=? AND path_key=? AND state=0"+MoveProtection(), source, entry.PathKey);
                        if (current.Count == 0 || (long)current[0][0] != entry.Generation || (string)current[0][1]!=entry.Kind || (string)current[0][2]!=entry.OldRelativePath) return;
                    }
                    Query("INSERT INTO outbox(source,epoch,sequence,request_id,payload) VALUES(?,?,?,?,?)", source, epoch, sequence, requestId, payload);
                    for (int i = 0; i < entries.Length; i++)
                        Query("INSERT INTO outbox_item VALUES(?,?,?,?)", source, i, entries[i].PathKey, entries[i].Generation);
                    frozen = true;
                });
                return frozen;
            }
        }

        public SyncOutbox Outbox(string source)
        {
            lock (gate)
            {
                List<object[]> rows = Query("SELECT epoch,sequence,request_id,payload,attempts,next_attempt_at,held FROM outbox WHERE source=?", source);
                if (rows.Count == 0) return null;
                object[] row = rows[0];
                return new SyncOutbox { SourceId = source, Epoch = (long)row[0], Sequence = (long)row[1], RequestId = (string)row[2], Payload = (string)row[3], Attempts = (int)(long)row[4], NextAttemptAt = (long)row[5], Held = (long)row[6] != 0 };
            }
        }

        // outcomes: 0=dirty stored, 1=observe again, 2=complete, 3=held.
        // Only call after validating the server signature and response identity.
        public void Acknowledge(string source, string requestId, int[] outcomes, long now)
        {
            lock (gate)
            {
                Transaction(delegate
                {
                    SyncOutbox box = Outbox(source);
                    if (box == null || box.RequestId != requestId) throw new InvalidOperationException("Outbox acknowledgement mismatch.");
                    List<object[]> entries = Query("SELECT path_key,generation FROM outbox_item WHERE source=? ORDER BY ordinal", source);
                    if (outcomes == null || outcomes.Length != entries.Count) throw new ArgumentException("Incomplete acknowledgement.");
                    for (int i = 0; i < entries.Count; i++)
                    {
                        string key = (string)entries[i][0]; long generation = (long)entries[i][1];
                        if (outcomes[i] == 0)
                            Query("UPDATE pending SET dirty_sent=1 WHERE source=? AND path_key=? AND generation=?", source, key, generation);
                        else if (outcomes[i] == 1)
                            Query("UPDATE pending SET dirty_sent=1,stable_count=1,due_at=? WHERE source=? AND path_key=? AND generation=?", checked(now + 10000), source, key, generation);
                        else if (outcomes[i] == 2)
                            Query("DELETE FROM pending WHERE source=? AND path_key=? AND generation=?", source, key, generation);
                        else if (outcomes[i] == 3)
                            Query("UPDATE pending SET state=1 WHERE source=? AND path_key=? AND generation=?", source, key, generation);
                        else throw new ArgumentException("Invalid acknowledgement outcome.");
                    }
                    Query("DELETE FROM outbox_item WHERE source=?", source);
                    Query("DELETE FROM outbox WHERE source=?", source);
                });
            }
        }

        public void Retry(string source, string requestId, long now, bool permanent)
        {
            lock (gate)
            {
                SyncOutbox box = Outbox(source);
                if (box == null || box.RequestId != requestId) throw new InvalidOperationException("Outbox retry mismatch.");
                long delay = Math.Min(300000L, 5000L * (1L << Math.Min(box.Attempts, 6)));
                Query("UPDATE outbox SET attempts=attempts+1,next_attempt_at=?,held=? WHERE source=? AND request_id=?",
                    checked(now + delay), permanent ? 1 : 0, source, requestId);
            }
        }

        public void EnqueueScope(string source, string path, string oldPath, string kind)
        {
            path = kind == "recovery" ? "" : NormalizePath(path);
            oldPath = String.IsNullOrEmpty(oldPath) ? null : NormalizePath(oldPath);
            lock (gate)
            {
                Transaction(delegate
                {
                    Query("UPDATE counter SET generation=generation+1 WHERE id=1");
                    long generation = (long)Query("SELECT generation FROM counter WHERE id=1")[0][0];
                    Query("INSERT OR REPLACE INTO scope VALUES(?,?,?,?,?,?)", source, UnicodeCaseFold.Fold(path), path, oldPath, kind, generation);
                });
            }
        }

        public SyncScope[] Scopes(string source, int limit)
        {
            if (limit < 1 || limit > 250) throw new ArgumentOutOfRangeException("limit");
            lock (gate)
            {
                List<SyncScope> results = new List<SyncScope>();
                foreach (object[] row in Query("SELECT path,old_path,kind,generation FROM scope WHERE source=? ORDER BY generation LIMIT ?", source, limit))
                    results.Add(new SyncScope { SourceId = source, RelativePath = (string)row[0], OldRelativePath = (string)row[1], Kind = (string)row[2], Generation = (long)row[3] });
                return results.ToArray();
            }
        }

        public void CompleteScope(SyncScope scope)
        {
            lock (gate) Query("DELETE FROM scope WHERE source=? AND path_key=? AND generation=?", scope.SourceId, UnicodeCaseFold.Fold(scope.RelativePath), scope.Generation);
        }

        private void FolderTables()
        {
            if(folderTablesReady)return;
            Query("CREATE TABLE IF NOT EXISTS folder_scope(source TEXT NOT NULL,path_key TEXT NOT NULL,path TEXT NOT NULL,old_path TEXT,kind TEXT NOT NULL,generation INTEGER NOT NULL,due_at INTEGER NOT NULL,absence_count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(source,path_key))");
            Query("CREATE TABLE IF NOT EXISTS folder_outbox(source TEXT PRIMARY KEY,epoch INTEGER NOT NULL,sequence INTEGER NOT NULL,request_id TEXT NOT NULL,payload TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at INTEGER NOT NULL DEFAULT 0,held INTEGER NOT NULL DEFAULT 0)");
            Query("CREATE TABLE IF NOT EXISTS folder_known(source TEXT NOT NULL,path_key TEXT NOT NULL,present INTEGER NOT NULL,PRIMARY KEY(source,path_key))");
            Query("CREATE TABLE IF NOT EXISTS move_hint(source TEXT NOT NULL,old_key TEXT NOT NULL,path_key TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(source,old_key))");
            Query("CREATE TABLE IF NOT EXISTS folder_move_origin(source TEXT NOT NULL,path_key TEXT NOT NULL,origin TEXT NOT NULL,origin_key TEXT NOT NULL,PRIMARY KEY(source,path_key,origin))");
            folderTablesReady=true;
        }

        public bool KnownFolder(string source,string path)
        {lock(gate){FolderTables();return Query("SELECT 1 FROM folder_known WHERE source=? AND path_key=? AND present=1",source,UnicodeCaseFold.Fold(NormalizePath(path))).Count!=0;}}

        public bool HasPendingMove(string source,string oldPath)
        {lock(gate){return Query("SELECT 1 FROM move_hint h JOIN pending m ON m.source=h.source AND m.path_key=h.path_key AND m.generation=h.generation AND m.kind='move' WHERE h.source=? AND h.old_key=? LIMIT 1",source,UnicodeCaseFold.Fold(NormalizePath(oldPath))).Count!=0;}}

        public bool SetMoveOrigin(SyncEntry entry,string oldPath)
        {
            oldPath=String.IsNullOrEmpty(oldPath)?null:NormalizePath(oldPath);
            lock(gate){bool changed=false;Transaction(delegate {
                var rows=Query("SELECT generation,kind,old_path FROM pending WHERE source=? AND path_key=?",entry.SourceId,entry.PathKey);
                if(rows.Count==0 || (long)rows[0][0]!=entry.Generation || (string)rows[0][1]=="delete")return;
                string kind=oldPath==null?"upsert":"move";
                if((string)rows[0][1]==kind && (string)rows[0][2]==oldPath){changed=true;return;}
                if(Query("SELECT 1 FROM outbox_item WHERE source=? AND path_key=? AND generation=?",entry.SourceId,entry.PathKey,entry.Generation).Count!=0){
                    Query("UPDATE counter SET generation=generation+1 WHERE id=1");entry.Generation=(long)Query("SELECT generation FROM counter WHERE id=1")[0][0];
                }
                Query("UPDATE pending SET kind=?,old_path=?,generation=? WHERE source=? AND path_key=?",kind,oldPath,entry.Generation,entry.SourceId,entry.PathKey);
                Query("DELETE FROM move_hint WHERE source=? AND path_key=?",entry.SourceId,entry.PathKey);
                if(oldPath!=null)Query("INSERT OR REPLACE INTO move_hint(source,old_key,path_key,generation) VALUES(?,?,?,?)",entry.SourceId,UnicodeCaseFold.Fold(oldPath),entry.PathKey,entry.Generation);
                entry.Kind=kind;entry.OldRelativePath=oldPath;changed=true;
            });return changed;}
        }

        public void ResetRejectedMove(string source,string path,long now,long rejectedGeneration,string rejectedOldPath)
        {
            lock(gate){string key=UnicodeCaseFold.Fold(NormalizePath(path));var rows=Query("SELECT generation,kind,old_path FROM pending WHERE source=? AND path_key=?",source,key);
                if(rows.Count==0 || (string)rows[0][1]!="move" || (long)rows[0][0]!=rejectedGeneration || (string)rows[0][2]!=rejectedOldPath)return;
                var entry=new SyncEntry{SourceId=source,PathKey=key,RelativePath=path,Generation=(long)rows[0][0]};
                if(SetMoveOrigin(entry,null))Query("UPDATE pending SET dirty_sent=0,due_at=? WHERE source=? AND path_key=? AND generation=?",now,source,key,entry.Generation);
            }
        }

        public long PendingGeneration(string source,string path)
        {lock(gate){var rows=Query("SELECT generation FROM pending WHERE source=? AND path_key=?",source,UnicodeCaseFold.Fold(NormalizePath(path)));return rows.Count==0?0:(long)rows[0][0];}}

        public bool RetireIntermediateFile(string source,string path,long expectedGeneration)
        {
            lock(gate){bool ready=false;Transaction(delegate {
                string key=UnicodeCaseFold.Fold(NormalizePath(path));
                if(Query("SELECT 1 FROM outbox_item WHERE source=? AND path_key=?",source,key).Count!=0)return;
                var rows=Query("SELECT generation FROM pending WHERE source=? AND path_key=?",source,key);
                if((rows.Count==0?0:(long)rows[0][0])!=expectedGeneration)return;
                Query("DELETE FROM pending WHERE source=? AND path_key=? AND kind IN ('move','upsert') AND generation=?",source,key,expectedGeneration);ready=true;
            });return ready;}
        }

        public string[] FolderMoveOrigins(string source,string path)
        {lock(gate){var origins=new List<string>();foreach(object[] row in Query("SELECT origin FROM folder_move_origin WHERE source=? AND path_key=?",source,UnicodeCaseFold.Fold(NormalizePath(path))))origins.Add((string)row[0]);return origins.ToArray();}}

        public void RememberFolder(string source,string path,bool present)
        {lock(gate){FolderTables();string key=UnicodeCaseFold.Fold(NormalizePath(path));
            Query("INSERT OR IGNORE INTO folder_known(source,path_key,present) VALUES(?,?,?)",source,key,present?1:0);
            Query("UPDATE folder_known SET present=? WHERE source=? AND path_key=? AND present<>?",present?1:0,source,key,present?1:0);}}

        public long NextGeneration()
        {
            lock (gate)
            {
                long value=0;
                Transaction(delegate { Query("UPDATE counter SET generation=generation+1 WHERE id=1"); value=(long)Query("SELECT generation FROM counter WHERE id=1")[0][0]; });
                return value;
            }
        }

        public void EnqueueFolderScope(string source, string path, string oldPath, string kind, long now)
        {
            if (kind != "create" && kind != "move" && kind != "delete" && kind != "recovery") throw new ArgumentException("Invalid folder scope.");
            path = kind == "recovery" ? "" : NormalizePath(path);
            oldPath = String.IsNullOrEmpty(oldPath) ? null : NormalizePath(oldPath);
            lock (gate)
            {
                FolderTables();
                Transaction(delegate {
                    string key=UnicodeCaseFold.Fold(path);
                    string intermediate=null;
                    if(kind=="move" && oldPath!=null) {
                        string oldKey=UnicodeCaseFold.Fold(oldPath);
                        var origins=new List<string>();origins.Add(oldPath);
                        foreach(object[] row in Query("SELECT origin FROM folder_move_origin WHERE source=? AND path_key=?",source,oldKey))origins.Add((string)row[0]);
                        var prior=Query("SELECT old_path FROM folder_scope WHERE source=? AND path_key=? AND kind='move'",source,oldKey);
                        if(prior.Count!=0 && prior[0][0]!=null) {
                            intermediate=oldPath;oldPath=(string)prior[0][0];origins.Add(oldPath);
                            Query("DELETE FROM folder_scope WHERE source=? AND path_key=?",source,oldKey);
                        }
                        Query("DELETE FROM folder_move_origin WHERE source=? AND path_key=?",source,key);
                        foreach(string origin in origins)Query("INSERT OR IGNORE INTO folder_move_origin(source,path_key,origin,origin_key) VALUES(?,?,?,?)",source,key,origin,UnicodeCaseFold.Fold(origin));
                    }
                    // Merge overlapping creates without allowing an older scan
                    // completion to discard a newer event captured mid-scan.
                    if (kind == "create")
                        foreach (object[] row in Query("SELECT path_key,path FROM folder_scope WHERE source=? AND kind='create'",source))
                        {
                            string ancestor=(string)row[0];
                            if (key.StartsWith(ancestor+"/",StringComparison.Ordinal)) { key=ancestor;path=(string)row[1];break; }
                        }
                    Query("UPDATE counter SET generation=generation+1 WHERE id=1");
                    long generation=(long)Query("SELECT generation FROM counter WHERE id=1")[0][0];
                    Query("INSERT OR REPLACE INTO folder_scope(source,path_key,path,old_path,kind,generation,due_at) VALUES(?,?,?,?,?,?,?)",source,key,path,oldPath,kind,generation,now);
                    if(intermediate!=null && UnicodeCaseFold.Fold(intermediate)!=key)
                        Query("INSERT OR REPLACE INTO folder_scope(source,path_key,path,old_path,kind,generation,due_at) VALUES(?,?,?,NULL,'delete',?,?)",source,UnicodeCaseFold.Fold(intermediate),intermediate,generation,now);
                    if(kind=="create")
                        foreach(object[] row in Query("SELECT path_key FROM folder_scope WHERE source=? AND kind='create'",source))
                            if(((string)row[0]).StartsWith(key+"/",StringComparison.Ordinal)) Query("DELETE FROM folder_scope WHERE source=? AND path_key=?",source,(string)row[0]);
                });
            }
        }

        public SyncScope[] FolderScopes(string source,long now,int limit)
        {return FolderScopesCore(source,now,limit,false);}

        public SyncScope[] FolderHeads(string source,long now,int limit)
        {return FolderScopesCore(source,now,limit,true);}

        private SyncScope[] FolderScopesCore(string source,long now,int limit,bool newest)
        {
            if(limit<1 || limit>250)throw new ArgumentOutOfRangeException("limit");
            lock(gate) {
                FolderTables();var result=new List<SyncScope>();
                foreach(object[] row in Query("SELECT path,old_path,kind,generation,due_at,absence_count FROM folder_scope WHERE source=? AND due_at<=? ORDER BY generation "+(newest?"DESC":"ASC")+" LIMIT ?",source,now,limit))
                    result.Add(new SyncScope {SourceId=source,RelativePath=(string)row[0],OldRelativePath=(string)row[1],Kind=(string)row[2],Generation=(long)row[3],DueAt=(long)row[4],AbsenceCount=(int)(long)row[5]});
                return result.ToArray();
            }
        }

        public void CompleteFolderScope(SyncScope scope)
        { lock(gate) { FolderTables(); Query("DELETE FROM folder_scope WHERE source=? AND path_key=? AND generation=?",scope.SourceId,UnicodeCaseFold.Fold(scope.RelativePath),scope.Generation); } }

        public void DelayFolderScope(SyncScope scope,long dueAt,int absenceCount)
        { lock(gate) { FolderTables(); Query("UPDATE folder_scope SET due_at=?,absence_count=? WHERE source=? AND path_key=? AND generation=?",dueAt,absenceCount,scope.SourceId,UnicodeCaseFold.Fold(scope.RelativePath),scope.Generation); } }

        public bool FreezeFolder(string source,long epoch,long sequence,string requestId,string payload)
        {
            if(Encoding.UTF8.GetByteCount(payload)>1048576)throw new ArgumentException("Folder batch too large.");
            lock(gate) { FolderTables(); bool frozen=false;Transaction(delegate {
                if(Query("SELECT source FROM folder_outbox WHERE source=?",source).Count>0)return;
                Query("INSERT INTO folder_outbox(source,epoch,sequence,request_id,payload) VALUES(?,?,?,?,?)",source,epoch,sequence,requestId,payload);frozen=true;
            });return frozen; }
        }

        public SyncOutbox FolderOutbox(string source)
        {
            lock(gate) { FolderTables();var rows=Query("SELECT epoch,sequence,request_id,payload,attempts,next_attempt_at,held FROM folder_outbox WHERE source=?",source);
                if(rows.Count==0)return null;object[] row=rows[0];
                return new SyncOutbox {SourceId=source,Epoch=(long)row[0],Sequence=(long)row[1],RequestId=(string)row[2],Payload=(string)row[3],Attempts=(int)(long)row[4],NextAttemptAt=(long)row[5],Held=(long)row[6]!=0}; }
        }

        public void AcknowledgeFolder(string source,string requestId)
        { lock(gate) { FolderTables();Query("DELETE FROM folder_outbox WHERE source=? AND request_id=?",source,requestId); } }

        public void RetryFolder(string source,string requestId,long now)
        { lock(gate) { FolderTables();var box=FolderOutbox(source);if(box==null || box.RequestId!=requestId)return;
            Query("UPDATE folder_outbox SET attempts=attempts+1,next_attempt_at=? WHERE source=? AND request_id=?",checked(now+Math.Min(30000L,1000L*(1L<<Math.Min(box.Attempts,5)))),source,requestId); } }

        public void Dispose()
        {
            lock (gate)
            {
                if (db != IntPtr.Zero) { Check(sqlite3_close_v2(db)); db = IntPtr.Zero; }
            }
        }
    }
}
