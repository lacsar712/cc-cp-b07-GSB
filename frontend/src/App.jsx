import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function statusLabel(s) {
  if (s === "done") return "已办结";
  if (s === "pending") return "候审";
  if (s === "processing") return "处理中";
  return s;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [view, setView] = useState("desk");
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [ship, setShip] = useState({ pending: [], packages: [], items: [], events: [] });
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const apiCall = useCallback(
    async (path, opts = {}) => {
      const res = await fetch(path, { headers: authHeaders(), ...opts });
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, status: res.status, data };
    },
    [authHeaders]
  );

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const { ok, data } = await apiCall("/api/readings");
    if (!ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(data);
  }, [token, apiCall]);

  const loadShipments = useCallback(async () => {
    if (!token) return;
    const { ok, data } = await apiCall("/api/shipments");
    if (ok) setShip(data);
  }, [token, apiCall]);

  useEffect(() => {
    if (!token) return undefined;
    loadReadings();
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [loadReadings, token]);

  useEffect(() => {
    if (!token || view !== "shipments") return undefined;
    loadShipments();
    const t = setInterval(loadShipments, 3000);
    return () => clearInterval(t);
  }, [loadShipments, token, view]);

  useEffect(() => {
    if (view === "shipments") loadShipments();
  }, [view, loadShipments]);

  async function onLogin(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
    setView("desk");
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const { ok, data } = await apiCall("/api/readings", {
        method: "POST",
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      if (!ok) {
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", temp_c: "" });
      await loadReadings();
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后判定合格或超温。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
            </div>
            {error && <p class="err">{error}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            记录员 logger / log123456 · 值班员 watcher / watch123456
          </p>
        </div>
      </div>
    );
  }

  const isWriter = user?.role === "writer";

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <nav class="nav">
            <button
              type="button"
              class={view === "desk" ? "navbtn on" : "navbtn"}
              onClick={() => { setView("desk"); setError(""); setMsg(""); }}
            >
              读数总表
            </button>
            <button
              type="button"
              class={view === "shipments" ? "navbtn on" : "navbtn"}
              onClick={() => { setView("shipments"); setError(""); setMsg(""); }}
            >
              装车捆扎
            </button>
          </nav>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "观察岗"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {error && <p class="err">{error}</p>}
      {msg && <p class="ok">{msg}</p>}

      {view === "desk" ? (
        <DeskView
          isWriter={isWriter}
          rows={rows}
          loading={loading}
          submitForm={submitForm}
          setSubmitForm={setSubmitForm}
          onSubmit={onSubmit}
          apiCall={apiCall}
          loadReadings={loadReadings}
          setError={setError}
          setMsg={setMsg}
        />
      ) : (
        <ShipmentsView
          isWriter={isWriter}
          ship={ship}
          apiCall={apiCall}
          loadShipments={loadShipments}
          loadReadings={loadReadings}
          setError={setError}
          setMsg={setMsg}
        />
      )}
    </div>
  );
}

function DeskView({
  isWriter, rows, loading, submitForm, setSubmitForm, onSubmit,
  apiCall, loadReadings, setError, setMsg,
}) {
  const [editId, setEditId] = useState(null);
  const [editTemp, setEditTemp] = useState("");
  const [savingId, setSavingId] = useState(null);

  async function saveTemp(id) {
    setError("");
    setMsg("");
    const value = parseFloat(editTemp);
    if (Number.isNaN(value)) {
      setError("温度必须是数字");
      return;
    }
    setSavingId(id);
    try {
      const { ok, data } = await apiCall(`/api/readings/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ temp_c: value }),
      });
      if (!ok) {
        setError(data.detail || "修改失败");
        return;
      }
      setMsg(`读数 #${id} 现行温度已改为 ${value}℃（总表现行数字随之变化）`);
      setEditId(null);
      await loadReadings();
    } finally {
      setSavingId(null);
    }
  }

  return (
    <>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
          <form onSubmit={onSubmit}>
            <div class="row">
              <label>
                探头编号
                <input
                  required
                  value={submitForm.probe_id}
                  onInput={(e) =>
                    setSubmitForm({ ...submitForm, probe_id: e.target.value })
                  }
                  placeholder="例如 探头C03"
                />
              </label>
              <label>
                温度（℃）
                <input
                  required
                  type="number"
                  step="0.1"
                  value={submitForm.temp_c}
                  onInput={(e) =>
                    setSubmitForm({ ...submitForm, temp_c: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                提交
              </button>
            </div>
          </form>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
          读数总表
          <span class="hint">（总表是现行数字，随时可修正；🔒 表示读数在某装车包内——改温只动总表，包内快照仍停在捆扎当刻）</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>编号</th>
              <th>探头</th>
              <th>现行温度℃</th>
              <th>结论</th>
              <th>说明</th>
              <th>状态</th>
              <th>提交人</th>
              <th>密封</th>
              {isWriter && <th>操作</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} class={r.sealed_package_id ? "sealed" : ""}>
                <td>{r.id}</td>
                <td>{r.probe_id}</td>
                <td>
                  {editId === r.id ? (
                    <span class="inlineedit">
                      <input
                        type="number"
                        step="0.1"
                        value={editTemp}
                        onInput={(e) => setEditTemp(e.target.value)}
                      />
                      <button type="button" disabled={savingId === r.id} onClick={() => saveTemp(r.id)}>
                        保存
                      </button>
                      <button type="button" class="secondary" onClick={() => setEditId(null)}>
                        取消
                      </button>
                    </span>
                  ) : (
                    r.temp_c
                  )}
                </td>
                <td>
                  <span class={verdictClass(r.verdict, r.status)}>
                    {displayVerdict(r)}
                  </span>
                </td>
                <td>{r.reason || "—"}</td>
                <td>{statusLabel(r.status)}</td>
                <td>{r.created_by}</td>
                <td>
                  {r.sealed_package_id ? (
                    <span class="tag lock">🔒 包#{r.sealed_package_id}</span>
                  ) : (
                    "—"
                  )}
                </td>
                {isWriter && (
                  <td>
                    {editId === r.id ? null : (
                      <button
                        type="button"
                        class="linkbtn"
                        title={r.sealed_package_id ? "只改总表现行数字，已冻包内快照不受影响" : ""}
                        onClick={() => { setEditId(r.id); setEditTemp(String(r.temp_c)); setError(""); }}
                      >
                        改温
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colspan={isWriter ? "9" : "8"}>暂无数据</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ShipmentsView({
  isWriter, ship, apiCall, loadShipments, loadReadings, setError, setMsg,
}) {
  const [selected, setSelected] = useState({});
  const [shipmentNo, setShipmentNo] = useState("");
  const [busy, setBusy] = useState(false);

  const itemsByPackage = new Map();
  for (const it of ship.items) {
    if (!itemsByPackage.has(it.package_id)) itemsByPackage.set(it.package_id, []);
    itemsByPackage.get(it.package_id).push(it);
  }

  const pendingIdSet = new Set(ship.pending.map((r) => r.id));
  const selectedIds = Object.entries(selected)
    .filter(([k, v]) => v && pendingIdSet.has(Number(k)))
    .map(([k]) => Number(k));

  function toggle(id, on) {
    setSelected((s) => ({ ...s, [id]: on }));
  }

  async function doBundle() {
    setError("");
    setMsg("");
    if (!shipmentNo.trim()) {
      setError("请填写装车单号");
      return;
    }
    if (selectedIds.length === 0) {
      setError("请至少勾选一笔已办结读数");
      return;
    }
    setBusy(true);
    try {
      const { ok, data } = await apiCall("/api/shipments/bundle", {
        method: "POST",
        body: JSON.stringify({ shipment_no: shipmentNo.trim(), reading_ids: selectedIds }),
      });
      if (!ok) {
        setError(data.detail || "捆扎失败");
        return;
      }
      setMsg(data.message || "已捆扎");
      setSelected({});
      setShipmentNo("");
      await Promise.all([loadShipments(), loadReadings()]);
    } finally {
      setBusy(false);
    }
  }

  async function doUnbundle(pkg) {
    setError("");
    setMsg("");
    if (!window.confirm(`确定拆捆装车包 #${pkg.id}（${pkg.shipment_no}）？读数将回到待捆列表，包内快照原样保留。`)) return;
    const { ok, data } = await apiCall(`/api/shipments/packages/${pkg.id}/unbundle`, {
      method: "POST",
    });
    if (!ok) {
      setError(data.detail || "拆捆失败");
      return;
    }
    setMsg(data.message || "已拆捆");
    await Promise.all([loadShipments(), loadReadings()]);
  }

  return (
    <>
      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
          待捆列表
          <span class="hint">（仅已办结读数可捆；候审/处理中不入捆）</span>
        </h2>
        <table>
          <thead>
            <tr>
              {isWriter && <th>勾选</th>}
              <th>编号</th>
              <th>探头</th>
              <th>温度℃</th>
              <th>结论</th>
              <th>办结时间</th>
            </tr>
          </thead>
          <tbody>
            {ship.pending.map((r) => (
              <tr key={r.id}>
                {isWriter && (
                  <td>
                    <input
                      type="checkbox"
                      checked={!!selected[r.id]}
                      onChange={(e) => toggle(r.id, e.target.checked)}
                    />
                  </td>
                )}
                <td>{r.id}</td>
                <td>{r.probe_id}</td>
                <td>{r.temp_c}</td>
                <td>
                  <span class={verdictClass(r.verdict, r.status)}>{r.verdict}</span>
                </td>
                <td>{fmtTime(r.processed_at)}</td>
              </tr>
            ))}
            {ship.pending.length === 0 && (
              <tr>
                <td colspan={isWriter ? "6" : "5"}>暂无可捆扎的已办结读数</td>
              </tr>
            )}
          </tbody>
        </table>
        {isWriter ? (
          <div class="row" style={{ marginTop: "0.9rem" }}>
            <label>
              装车单号
              <input
                value={shipmentNo}
                onInput={(e) => setShipmentNo(e.target.value)}
                placeholder="例如 ZC-20261002-01"
              />
            </label>
            <button type="button" disabled={busy || selectedIds.length === 0} onClick={doBundle}>
              一点捆扎（{selectedIds.length} 笔冻进包内）
            </button>
          </div>
        ) : (
          <p class="hint" style={{ marginBottom: 0 }}>观察岗可翻包查看，不能捆扎。</p>
        )}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
          已捆包
          <span class="hint">（包内为捆扎当刻快照，只读留档；拆捆不改快照）</span>
        </h2>
        {ship.packages.length === 0 && <p class="hint">暂无装车包。</p>}
        {ship.packages.map((p) => {
          const items = itemsByPackage.get(p.id) || [];
          return (
            <div key={p.id} class={p.status === "sealed" ? "pkg sealedpkg" : "pkg unbundledpkg"}>
              <div class="pkghead">
                <div>
                  <strong>包#{p.id}</strong>
                  <span class="shipno">装车单 {p.shipment_no}</span>
                  <span class={p.status === "sealed" ? "tag lock" : "tag done"}>
                    {p.status === "sealed" ? "🔒 密封中" : "已拆捆"}
                  </span>
                </div>
                <div class="hint">
                  捆扎人 {p.created_by} · 捆于 {fmtTime(p.created_at)}
                  {p.status === "unbundled" &&
                    ` · 拆捆人 ${p.unbundled_by} · 拆于 ${fmtTime(p.unbundled_at)}`}
                  {isWriter && p.status === "sealed" && (
                    <button
                      type="button"
                      class="secondary"
                      style={{ marginLeft: "0.75rem" }}
                      onClick={() => doUnbundle(p)}
                    >
                      拆捆
                    </button>
                  )}
                </div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>读数号</th>
                    <th>探头</th>
                    <th>冻存温度℃</th>
                    <th>冻存结论</th>
                    <th>说明</th>
                    <th>冻存时刻</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => (
                    <tr key={it.id}>
                      <td>{it.reading_id}</td>
                      <td>{it.probe_id}</td>
                      <td class="frozenval">{it.temp_c}</td>
                      <td>
                        <span class={verdictClass(it.verdict, "done")}>{it.verdict}</span>
                      </td>
                      <td>{it.reason || "—"}</td>
                      <td>{fmtTime(it.frozen_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>捆扎 / 拆捆流水</h2>
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>事件</th>
              <th>装车包</th>
              <th>装车单号</th>
              <th>操作人</th>
              <th>明细</th>
            </tr>
          </thead>
          <tbody>
            {ship.events.map((ev) => {
              let count = null;
              try {
                count = JSON.parse(ev.detail || "{}").count;
              } catch {
                count = null;
              }
              return (
                <tr key={ev.id}>
                  <td>{fmtTime(ev.created_at)}</td>
                  <td>
                    <span class={ev.event_type === "bundled" ? "tag pass" : "tag wait"}>
                      {ev.event_type === "bundled" ? "捆扎" : "拆捆"}
                    </span>
                  </td>
                  <td>包#{ev.package_id}</td>
                  <td>{ev.shipment_no}</td>
                  <td>{ev.actor}</td>
                  <td>{count != null ? `${count} 笔读数` : "—"}</td>
                </tr>
              );
            })}
            {ship.events.length === 0 && (
              <tr>
                <td colspan="6">暂无流水</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
