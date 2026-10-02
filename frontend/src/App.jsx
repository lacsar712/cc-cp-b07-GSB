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

function fmt(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return ts;
  }
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
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(await res.json());
  }, [token, authHeaders]);

  useEffect(() => {
    loadReadings();
    if (!token) return undefined;
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [loadReadings, token]);

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
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
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
            记录员 logger / log123456 · 观察岗 watcher / watch123456
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
          <p class="sub">温度不超过 8℃ 为合格，否则为超温。</p>
        </div>
        <div class="user">
          <nav class="nav">
            <button
              type="button"
              class={view === "desk" ? "navbtn active" : "navbtn"}
              onClick={() => setView("desk")}
            >
              读数总表
            </button>
            <button
              type="button"
              class={view === "loading" ? "navbtn active" : "navbtn"}
              onClick={() => setView("loading")}
            >
              装车捆扎
            </button>
          </nav>
          {user?.username}（{isWriter ? "记录员" : "观察岗"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {view === "loading" ? (
        <LoadingPage
          isWriter={isWriter}
          authHeaders={authHeaders}
          onChanged={loadReadings}
          setGlobalError={setError}
          setGlobalMsg={setMsg}
        />
      ) : (
        <DeskView
          isWriter={isWriter}
          rows={rows}
          loading={loading}
          error={error}
          msg={msg}
          submitForm={submitForm}
          setSubmitForm={setSubmitForm}
          onSubmit={onSubmit}
          authHeaders={authHeaders}
          onChanged={loadReadings}
          setError={setError}
          goLoading={() => setView("loading")}
        />
      )}
    </div>
  );
}

function DeskView(props) {
  const {
    isWriter, rows, loading, error, msg, submitForm, setSubmitForm,
    onSubmit, authHeaders, onChanged, setError, goLoading,
  } = props;
  const [editId, setEditId] = useState(null);
  const [editTemp, setEditTemp] = useState("");
  const [editErr, setEditErr] = useState("");
  const [savingId, setSavingId] = useState(null);

  async function saveTemp(id) {
    setEditErr("");
    const val = parseFloat(editTemp);
    if (Number.isNaN(val)) {
      setEditErr("温度必须是数字");
      return;
    }
    setSavingId(id);
    try {
      const res = await fetch(`/api/readings/${id}`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify({ temp_c: val }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // 冻包读数走写口必被拒：把后端原因留在该行，证明不是只冻展示。
        setEditErr(data.detail || "修订失败");
        return;
      }
      setEditId(null);
      setEditTemp("");
      await onChanged();
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
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
          读数总表
          <button type="button" class="navbtn" style={{ marginLeft: "0.75rem" }} onClick={goLoading}>
            前往装车捆扎
          </button>
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
              <th>装车</th>
              {isWriter && <th>改温</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} class={r.frozen ? "frozen" : ""}>
                <td>{r.id}</td>
                <td>{r.probe_id}</td>
                <td>{r.temp_c}</td>
                <td>
                  <span class={verdictClass(r.verdict, r.status)}>
                    {displayVerdict(r)}
                  </span>
                </td>
                <td>{r.reason || "—"}</td>
                <td>{r.status}</td>
                <td>{r.created_by}</td>
                <td>
                  {r.frozen ? (
                    <span class="tag ice">已冻 · {r.loading_no}</span>
                  ) : (
                    "—"
                  )}
                </td>
                {isWriter && (
                  <td>
                    {r.frozen ? (
                      <span class="locktip" title="包内读数禁止经由写接口改写">🔒 封写</span>
                    ) : editId === r.id ? (
                      <span class="inlineedit">
                        <input
                          type="number"
                          step="0.1"
                          value={editTemp}
                          onInput={(e) => setEditTemp(e.target.value)}
                          style={{ minWidth: "90px" }}
                        />
                        <button
                          type="button"
                          disabled={savingId === r.id}
                          onClick={() => saveTemp(r.id)}
                        >
                          存
                        </button>
                        <button
                          type="button"
                          class="secondary"
                          onClick={() => {
                            setEditId(null);
                            setEditErr("");
                          }}
                        >
                          消
                        </button>
                        {editErr && <span class="err" style={{ marginLeft: "0.4rem" }}>{editErr}</span>}
                      </span>
                    ) : (
                      <button
                        type="button"
                        class="navbtn"
                        onClick={() => {
                          setEditId(r.id);
                          setEditTemp(String(r.temp_c));
                          setEditErr("");
                        }}
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
                <td colspan={isWriter ? 9 : 8}>暂无数据</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function LoadingPage(props) {
  const { isWriter, authHeaders, onChanged, setGlobalError, setGlobalMsg } = props;
  const [data, setData] = useState({ pending: [], packages: [], logs: [] });
  const [picked, setPicked] = useState(() => new Set());
  const [loadingNo, setLoadingNo] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [openPkg, setOpenPkg] = useState(() => new Set());

  const load = useCallback(async () => {
    const res = await fetch("/api/loading/overview", { headers: authHeaders() });
    if (!res) return;
    if (res.ok) {
      const d = await res.json();
      setData(d);
      // 清掉已不在待捆列表中的勾选项（捆走或状态变化）。
      setPicked((prev) => {
        const ids = new Set(d.pending.map((p) => p.id));
        const next = new Set();
        prev.forEach((id) => ids.has(id) && next.add(id));
        return next;
      });
    } else {
      setGlobalError("加载装车捆扎数据失败");
    }
  }, [authHeaders, setGlobalError]);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  function toggle(id) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function seal(e) {
    e.preventDefault();
    setErr("");
    setOk("");
    const ids = Array.from(picked).sort((a, b) => a - b);
    if (!loadingNo.trim()) {
      setErr("请填写装车单号");
      return;
    }
    if (ids.length === 0) {
      setErr("请勾选至少一笔已办结读数");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/loading/packages", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ loading_no: loadingNo.trim(), reading_ids: ids }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErr(d.detail || "捆扎失败");
        return;
      }
      setOk(d.message || "已捆扎");
      setPicked(new Set());
      setLoadingNo("");
      await load();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function unbundle(pkg) {
    if (!window.confirm(`确认拆捆装车单 ${pkg.loading_no}？读数将回到待捆列表。`)) return;
    setErr("");
    setBusy(true);
    try {
      const res = await fetch(`/api/loading/packages/${pkg.id}/unbundle`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({}),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErr(d.detail || "拆捆失败");
        return;
      }
      setOk(d.message || "已拆捆");
      await load();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  function togglePkg(id) {
    setOpenPkg((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const allChecked =
    data.pending.length > 0 && data.pending.every((p) => picked.has(p.id));

  return (
    <>
      {!isWriter && (
        <div class="card rohint">
          观察岗（只读）：可翻阅全部装车包与拆捆流水，但不能捆扎或拆捆。
        </div>
      )}
      {err && <p class="err">{err}</p>}
      {ok && <p class="ok">{ok}</p>}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>待捆列表（仅已办结读数）</h2>
        {isWriter && (
          <form onSubmit={seal} class="sealbar">
            <label>
              装车单号
              <input
                value={loadingNo}
                onInput={(e) => setLoadingNo(e.target.value)}
                placeholder="例如 ZC-20261002-01"
              />
            </label>
            <button type="submit" disabled={busy || picked.size === 0}>
              一点捆扎（{picked.size}）
            </button>
          </form>
        )}
        <table>
          <thead>
            <tr>
              {isWriter && (
                <th>
                  <input
                    type="checkbox"
                    style={{ minWidth: 0 }}
                    checked={allChecked}
                    onChange={() =>
                      setPicked(allChecked ? new Set() : new Set(data.pending.map((p) => p.id)))
                    }
                  />
                </th>
              )}
              <th>编号</th>
              <th>探头</th>
              <th>温度℃</th>
              <th>结论</th>
              <th>状态</th>
              <th>提交人</th>
              <th>办结时间</th>
            </tr>
          </thead>
          <tbody>
            {data.pending.map((p) => (
              <tr key={p.id}>
                {isWriter && (
                  <td>
                    <input
                      type="checkbox"
                      style={{ minWidth: 0 }}
                      checked={picked.has(p.id)}
                      onChange={() => toggle(p.id)}
                    />
                  </td>
                )}
                <td>{p.id}</td>
                <td>{p.probe_id}</td>
                <td>{p.temp_c}</td>
                <td>
                  <span class={verdictClass(p.verdict, p.status)}>{p.verdict}</span>
                </td>
                <td>{p.status}</td>
                <td>{p.created_by}</td>
                <td>{fmt(p.processed_at)}</td>
              </tr>
            ))}
            {data.pending.length === 0 && (
              <tr>
                <td colspan={isWriter ? 8 : 7}>暂无待捆的已办结读数</td>
              </tr>
            )}
          </tbody>
        </table>
        {isWriter && (
          <p class="sub" style={{ margin: "0.5rem 0 0" }}>
            候审或处理中的读数不出现在此列表，不得入捆。
          </p>
        )}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>已捆包（包内读数停在捆扎当刻）</h2>
        {data.packages.length === 0 && <p class="sub" style={{ marginBottom: 0 }}>暂无已捆装车包。</p>}
        {data.packages.map((pkg) => (
          <div key={pkg.id} class="pkg">
            <div class="pkghead">
              <button type="button" class="navbtn" onClick={() => togglePkg(pkg.id)}>
                {openPkg.has(pkg.id) ? "▼" : "▶"} 翻包
              </button>
              <strong>{pkg.loading_no}</strong>
              <span class="tag ice">已冻 {pkg.items.length} 笔</span>
              <span class="pkgsub">
                捆扎人 {pkg.sealed_by} · {fmt(pkg.sealed_at)}
              </span>
              {isWriter && (
                <button
                  type="button"
                  class="secondary"
                  style={{ marginLeft: "auto" }}
                  disabled={busy}
                  onClick={() => unbundle(pkg)}
                >
                  拆捆
                </button>
              )}
            </div>
            {openPkg.has(pkg.id) && (
              <table>
                <thead>
                  <tr>
                    <th>读数编号</th>
                    <th>探头</th>
                    <th>定格温度℃</th>
                    <th>定格结论</th>
                    <th>现行温度℃</th>
                    <th>现行状态</th>
                    <th>提交人</th>
                    <th>捆扎当刻</th>
                  </tr>
                </thead>
                <tbody>
                  {pkg.items.map((it) => (
                    <tr key={it.reading_id}>
                      <td>{it.reading_id}</td>
                      <td>{it.probe_id}</td>
                      <td>
                        <strong>{it.temp_c}</strong>
                        {it.current_temp_c !== null &&
                          Number(it.current_temp_c) !== Number(it.temp_c) && (
                            <span class="drift">（现行 {it.current_temp_c}）</span>
                          )}
                      </td>
                      <td>
                        <span class={verdictClass(it.verdict, it.reading_status)}>
                          {it.verdict}
                        </span>
                      </td>
                      <td>{it.current_temp_c}</td>
                      <td>{it.current_status}</td>
                      <td>{it.created_by}</td>
                      <td>{fmt(it.sealed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ))}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>拆捆流水</h2>
        <table>
          <thead>
            <tr>
              <th>序号</th>
              <th>装车单号</th>
              <th>释放读数</th>
              <th>拆捆人</th>
              <th>拆捆时间</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {data.logs.map((l) => (
              <tr key={l.id}>
                <td>{l.id}</td>
                <td>{l.loading_no}</td>
                <td>{(l.reading_ids || []).join(", ")}</td>
                <td>{l.unbundled_by}</td>
                <td>{fmt(l.unbundled_at)}</td>
                <td>{l.reason || "—"}</td>
              </tr>
            ))}
            {data.logs.length === 0 && (
              <tr>
                <td colspan="6">暂无拆捆记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
