"use client";

import {
  Check,
  ChevronDown,
  Clipboard,
  Copy,
  ExternalLink,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  X
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

type PanelSettings = Record<string, string | number | boolean>;

type Config = {
  id: number;
  token: string;
  public_url: string;
  upstream_url: string;
  volume_gb: number;
  volume_text: string;
  category_key: string;
  is_sold: boolean;
  service_name: string;
  panel_username: string;
  source_panel_key: string;
  telegram_user_id: number | null;
  profile_title: string;
  profile_title_locked: boolean;
  device_limit: number;
  device_count: number;
  show_header: boolean;
  channel_handle: string;
  show_config_preview: boolean;
  info_proxies_enabled: boolean;
  address_rewrites: string;
  search: string;
};

const blankConfig = {
  upstream_url: "",
  token: "",
  service_name: "",
  panel_username: "",
  profile_title: "",
  device_limit: 0,
  show_header: true,
  channel_handle: "",
  show_config_preview: true,
  info_proxies_enabled: false,
  address_rewrites: "",
  volume_gb: 0,
  category_key: "manual"
};

const settingLabels: Record<string, string> = {
  brand_name: "نام برند",
  subscription_profile_title: "نام نمایشی پیش فرض داخل برنامه ها",
  subscription_device_limit: "محدودیت دستگاه پیش فرض",
  primary_color: "رنگ اصلی",
  accent_color: "رنگ وضعیت",
  background_color: "رنگ پس زمینه",
  card_color: "رنگ کارت ها",
  text_color: "رنگ متن اصلی",
  muted_text_color: "رنگ متن فرعی",
  secondary_button_color: "رنگ دکمه فرعی",
  channel_handle: "کانال پیش فرض",
  hero_text: "متن بالای صفحه",
  support_text: "متن پایین صفحه",
  active_status_text: "متن وضعیت فعال",
  purchased_volume_label: "عنوان حجم خریداری شده",
  used_label: "عنوان مصرف شده",
  remaining_label: "عنوان باقی مانده",
  expiry_label: "عنوان تاریخ انقضا",
  config_count_label: "عنوان تعداد کانفیگ",
  subscription_title: "عنوان لینک اشتراک",
  copy_button_text: "متن دکمه کپی لینک",
  copy_success_text: "پیام موفقیت کپی",
  qr_button_text: "متن دکمه QR",
  apps_title: "عنوان اتصال سریع",
  apps_help_text: "راهنمای اتصال سریع",
  quick_connect_order: "ترتیب اتصال سریع",
  v2rayng_button_text: "متن V2RayNG",
  hiddify_button_text: "متن Hiddify",
  streisand_button_text: "متن Streisand",
  singbox_button_text: "متن Sing-box",
  v2box_button_text: "متن V2Box",
  happ_button_text: "متن HAPP",
  channel_button_text: "متن دکمه کانال",
  copy_button_color: "رنگ کپی لینک",
  qr_button_color: "رنگ QR لینک",
  v2rayng_button_color: "رنگ V2RayNG",
  hiddify_button_color: "رنگ Hiddify",
  streisand_button_color: "رنگ Streisand",
  singbox_button_color: "رنگ Sing-box",
  v2box_button_color: "رنگ V2Box",
  happ_button_color: "رنگ HAPP",
  channel_button_color: "رنگ کانال",
  configs_title: "عنوان کانفیگ ها",
  config_copy_button_text: "متن کپی کانفیگ",
  config_qr_button_text: "متن QR کانفیگ",
  config_copy_button_color: "رنگ کپی کانفیگ",
  config_qr_button_color: "رنگ QR کانفیگ",
  empty_configs_text: "متن نبود کانفیگ",
  show_quick_connect: "نمایش اتصال سریع",
  show_channel_button: "نمایش دکمه کانال",
  show_config_preview: "نمایش کانفیگ ها",
  show_config_copy: "نمایش کپی کانفیگ",
  show_config_qr: "نمایش QR کانفیگ"
};

const textAreaSettings = new Set(["hero_text", "support_text", "apps_help_text", "empty_configs_text"]);

async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...options,
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      ...(options.headers || {})
    }
  });
  if (!response.ok) {
    let detail = "درخواست انجام نشد.";
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  return response.json();
}

function Button({
  children,
  variant = "default",
  busy = false,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "secondary" | "danger" | "ghost"; busy?: boolean }) {
  return (
    <button className={`button ${variant}`} disabled={busy || props.disabled} {...props}>
      {busy ? <LoaderCircle className="spin" size={17} /> : children}
    </button>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label>
      <span>{label}</span>
      {children}
      {hint ? <small className="field-help">{hint}</small> : null}
    </label>
  );
}

export default function AdminPage() {
  const [settings, setSettings] = useState<PanelSettings>({});
  const [configs, setConfigs] = useState<Config[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [editing, setEditing] = useState<Config | null>(null);
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>(blankConfig);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState<PanelSettings>({});
  const [bulkDraft, setBulkDraft] = useState({ show_header: true, show_config_preview: true, info_proxies_enabled: false });

  async function load() {
    setBusy("load");
    setError("");
    try {
      const payload = await api<{ settings: PanelSettings; configs: Config[] }>("/admin/api/bootstrap");
      setSettings(payload.settings);
      setSettingsDraft(payload.settings);
      setConfigs(payload.configs);
      setSelected((items) => items.filter((id) => payload.configs.some((config) => config.id === id)));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "دریافت اطلاعات انجام نشد.");
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase("fa-IR");
    if (!term) return configs;
    return configs.filter((config) => config.search.toLocaleLowerCase("fa-IR").includes(term));
  }, [configs, query]);

  function flash(message: string, tone: "ok" | "error" = "ok") {
    if (tone === "ok") {
      setNotice(message);
      setError("");
    } else {
      setError(message);
      setNotice("");
    }
    window.setTimeout(() => {
      setNotice("");
      setError("");
    }, 4200);
  }

  function edit(config: Config) {
    setEditing(config);
    setDraft({
      upstream_url: config.upstream_url,
      service_name: config.service_name,
      panel_username: config.panel_username,
      profile_title: config.profile_title,
      device_limit: config.device_limit,
      show_header: config.show_header,
      channel_handle: config.channel_handle,
      show_config_preview: config.show_config_preview,
      info_proxies_enabled: config.info_proxies_enabled,
      address_rewrites: config.address_rewrites,
      volume_gb: config.volume_gb,
      category_key: config.category_key
    });
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("create");
    try {
      const payload = await api<{ config: Config }>("/admin/api/subscriptions", {
        method: "POST",
        body: JSON.stringify(draft)
      });
      setConfigs((items) => [payload.config, ...items.filter((item) => item.id !== payload.config.id)]);
      setDraft(blankConfig);
      flash("لینک اختصاصی ساخته شد.");
    } catch (reason) {
      flash(reason instanceof Error ? reason.message : "ساخت لینک انجام نشد.", "error");
    } finally {
      setBusy("");
    }
  }

  async function saveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    setBusy(`edit-${editing.id}`);
    try {
      const payload = await api<{ config: Config }>(`/admin/api/subscriptions/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify(draft)
      });
      setConfigs((items) => items.map((item) => (item.id === payload.config.id ? payload.config : item)));
      setEditing(null);
      flash("تغییرات لینک ذخیره شد.");
    } catch (reason) {
      flash(reason instanceof Error ? reason.message : "ذخیره تغییرات انجام نشد.", "error");
    } finally {
      setBusy("");
    }
  }

  async function removeConfig(config: Config) {
    if (!window.confirm(`لینک «${config.service_name || config.token}» حذف شود؟`)) return;
    setBusy(`delete-${config.id}`);
    try {
      await api(`/admin/api/subscriptions/${config.id}`, { method: "DELETE" });
      setConfigs((items) => items.filter((item) => item.id !== config.id));
      setSelected((items) => items.filter((id) => id !== config.id));
      flash("لینک حذف شد.");
    } catch (reason) {
      flash(reason instanceof Error ? reason.message : "حذف انجام نشد.", "error");
    } finally {
      setBusy("");
    }
  }

  async function action(config: Config, actionName: "devices/reset" | "revoke") {
    const label = actionName === "revoke" ? "باطل و لینک جدید ساخته شود؟" : "شمارش دستگاه ها ریست شود؟";
    if (!window.confirm(label)) return;
    setBusy(`${actionName}-${config.id}`);
    try {
      const payload = await api<{ public_url?: string }>(`/admin/api/subscriptions/${config.id}/${actionName}`, { method: "POST" });
      await load();
      if (payload.public_url) await navigator.clipboard.writeText(payload.public_url);
      flash(actionName === "revoke" ? "لینک جدید ساخته شد و کپی شد." : "شمارش دستگاه ریست شد.");
    } catch (reason) {
      flash(reason instanceof Error ? reason.message : "عملیات انجام نشد.", "error");
    } finally {
      setBusy("");
    }
  }

  async function bulkDelete() {
    if (!selected.length || !window.confirm(`${selected.length} لینک انتخاب شده حذف شود؟`)) return;
    setBusy("bulk-delete");
    try {
      const payload = await api<{ deleted_ids: number[] }>("/admin/api/subscriptions/bulk-delete", {
        method: "POST",
        body: JSON.stringify({ ids: selected })
      });
      setConfigs((items) => items.filter((item) => !payload.deleted_ids.includes(item.id)));
      setSelected([]);
      flash("لینک های انتخاب شده حذف شدند.");
    } catch (reason) {
      flash(reason instanceof Error ? reason.message : "حذف گروهی انجام نشد.", "error");
    } finally {
      setBusy("");
    }
  }

  async function bulkApply() {
    if (!selected.length) return;
    setBusy("bulk-update");
    try {
      const payload = await api<{ configs: Config[] }>("/admin/api/subscriptions/bulk-update", {
        method: "POST",
        body: JSON.stringify({ ids: selected, patch: bulkDraft })
      });
      setConfigs(payload.configs);
      flash("تنظیمات انتخابی روی لینک ها اعمال شد.");
    } catch (reason) {
      flash(reason instanceof Error ? reason.message : "ویرایش گروهی انجام نشد.", "error");
    } finally {
      setBusy("");
    }
  }

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("settings");
    try {
      const payload = await api<{ settings: PanelSettings }>("/admin/api/settings", {
        method: "POST",
        body: JSON.stringify(settingsDraft)
      });
      setSettings(payload.settings);
      setSettingsDraft(payload.settings);
      flash("تنظیمات قالب ذخیره شد.");
    } catch (reason) {
      flash(reason instanceof Error ? reason.message : "ذخیره تنظیمات انجام نشد.", "error");
    } finally {
      setBusy("");
    }
  }

  const allVisibleSelected = filtered.length > 0 && filtered.every((item) => selected.includes(item.id));

  return (
    <main className="admin-shell">
      <aside className="rail">
        <div className="brand">PH</div>
        <nav>
          <a href="#create"><Plus size={18} /> ساخت</a>
          <a href="#settings"><Settings size={18} /> قالب</a>
          <a href="#links"><ShieldCheck size={18} /> لینک ها</a>
        </nav>
      </aside>

      <section className="workspace">
        <header className="page-head">
          <div>
            <p>Phantom Subscription</p>
            <h1>مدیریت پنل ساب</h1>
          </div>
          <Button variant="secondary" onClick={load} busy={busy === "load"}>
            <RefreshCw size={17} /> بروزرسانی
          </Button>
        </header>

        {(notice || error) && <div className={`toast ${error ? "error" : "ok"}`}>{error || notice}</div>}

        <section id="create" className="panel">
          <div className="panel-head">
            <div><h2>تبدیل دستی لینک ساب</h2><p>لینک اختصاصی بدون رفرش صفحه ساخته می شود.</p></div>
          </div>
          <form onSubmit={create} className="form-grid">
            <Field label="لینک اصلی سابسکریپشن" hint="لینک اصلی که از پنل یا تامین‌کننده خوانده می‌شود."><input dir="ltr" required value={String(draft.upstream_url)} onChange={(event) => setDraft({ ...draft, upstream_url: event.target.value })} /></Field>
            <Field label="توکن دلخواه"><input dir="ltr" value={String(draft.token || "")} onChange={(event) => setDraft({ ...draft, token: event.target.value })} /></Field>
            <Field label="نام سرویس"><input value={String(draft.service_name)} onChange={(event) => setDraft({ ...draft, service_name: event.target.value })} /></Field>
            <Field label="Username پنل برای کانفیگ آدمک"><input value={String(draft.panel_username)} onChange={(event) => setDraft({ ...draft, panel_username: event.target.value })} /></Field>
            <Field label="نام نمایشی داخل برنامه ها"><input value={String(draft.profile_title)} onChange={(event) => setDraft({ ...draft, profile_title: event.target.value })} /></Field>
            <Field label="محدودیت کاربر/دستگاه"><input type="number" min="0" value={Number(draft.device_limit)} onChange={(event) => setDraft({ ...draft, device_limit: Number(event.target.value || 0) })} /></Field>
            <Field label="حجم گیگ"><input type="number" min="0" value={Number(draft.volume_gb)} onChange={(event) => setDraft({ ...draft, volume_gb: Number(event.target.value || 0) })} /></Field>
            <Field label="دسته بندی"><input value={String(draft.category_key)} onChange={(event) => setDraft({ ...draft, category_key: event.target.value })} /></Field>
            <Field label="کانال/پشتیبانی اختصاصی"><input value={String(draft.channel_handle)} onChange={(event) => setDraft({ ...draft, channel_handle: event.target.value })} /></Field>
            <Field label="بازنویسی آدرس سرورهای داخل کانفیگ" hint="این بخش لینک اصلی را عوض نمی‌کند؛ فقط دامنه یا IP داخل کانفیگ‌ها را با الگوی old-host=new-host جایگزین می‌کند."><textarea dir="ltr" value={String(draft.address_rewrites)} onChange={(event) => setDraft({ ...draft, address_rewrites: event.target.value })} /></Field>
            <div className="switch-row wide">
              {(["show_header", "show_config_preview", "info_proxies_enabled"] as const).map((key) => (
                <label className="switch" key={key}>
                  <input type="checkbox" checked={Boolean(draft[key])} onChange={(event) => setDraft({ ...draft, [key]: event.target.checked })} />
                  <span>{key === "show_header" ? "نمایش هدر" : key === "show_config_preview" ? "نمایش کانفیگ ها" : "کانفیگ اطلاعاتی"}</span>
                </label>
              ))}
            </div>
            <div className="actions wide"><Button type="submit" busy={busy === "create"}><Plus size={17} /> ساخت لینک</Button></div>
          </form>
        </section>

        <section id="settings" className="panel">
          <button className="collapse-head" onClick={() => setSettingsOpen(!settingsOpen)}>
            <span><SlidersHorizontal size={18} /> تنظیمات قالب</span>
            <ChevronDown className={settingsOpen ? "up" : ""} size={18} />
          </button>
          {settingsOpen && (
            <form onSubmit={saveSettings} className="form-grid settings-grid">
              {Object.entries(settingsDraft).map(([key, value]) => {
                const label = settingLabels[key] || key;
                if (typeof value === "boolean") {
                  return (
                    <label className="switch" key={key}>
                      <input type="checkbox" checked={Boolean(value)} onChange={(event) => setSettingsDraft({ ...settingsDraft, [key]: event.target.checked })} />
                      <span>{label}</span>
                    </label>
                  );
                }
                if (key.endsWith("_color") || key === "primary_color" || key === "accent_color" || key === "background_color" || key === "card_color" || key === "text_color" || key === "muted_text_color") {
                  return <Field label={label} key={key}><input type="color" value={String(value)} onChange={(event) => setSettingsDraft({ ...settingsDraft, [key]: event.target.value })} /></Field>;
                }
                if (textAreaSettings.has(key)) {
                  return <Field label={label} key={key}><textarea value={String(value)} onChange={(event) => setSettingsDraft({ ...settingsDraft, [key]: event.target.value })} /></Field>;
                }
                return <Field label={label} key={key}><input dir={key.includes("order") ? "ltr" : "rtl"} type={typeof value === "number" ? "number" : "text"} value={String(value)} onChange={(event) => setSettingsDraft({ ...settingsDraft, [key]: typeof value === "number" ? Number(event.target.value || 0) : event.target.value })} /></Field>;
              })}
              <div className="actions wide"><Button type="submit" busy={busy === "settings"}><Save size={17} /> ذخیره قالب</Button></div>
            </form>
          )}
        </section>

        <section id="links" className="panel">
          <div className="panel-head sticky-tools">
            <div><h2>لینک های ثبت شده</h2><p>{filtered.length.toLocaleString("fa-IR")} از {configs.length.toLocaleString("fa-IR")} لینک</p></div>
            <div className="search-box"><Search size={17} /><input placeholder="جستجو بر اساس اسم، لینک اصلی، لینک ساخته شده..." value={query} onChange={(event) => setQuery(event.target.value)} /></div>
          </div>

          <div className="bulk-bar">
            <label className="checkline">
              <input type="checkbox" checked={allVisibleSelected} onChange={(event) => setSelected(event.target.checked ? filtered.map((item) => item.id) : [])} />
              <span>{selected.length ? `${selected.length.toLocaleString("fa-IR")} انتخاب شده` : "انتخاب همه موارد نمایش داده شده"}</span>
            </label>
            <div className="bulk-options">
              <label><input type="checkbox" checked={bulkDraft.show_header} onChange={(event) => setBulkDraft({ ...bulkDraft, show_header: event.target.checked })} /> هدر</label>
              <label><input type="checkbox" checked={bulkDraft.show_config_preview} onChange={(event) => setBulkDraft({ ...bulkDraft, show_config_preview: event.target.checked })} /> کانفیگ ها</label>
              <label><input type="checkbox" checked={bulkDraft.info_proxies_enabled} onChange={(event) => setBulkDraft({ ...bulkDraft, info_proxies_enabled: event.target.checked })} /> اطلاعاتی</label>
            </div>
            <Button variant="secondary" onClick={bulkApply} disabled={!selected.length} busy={busy === "bulk-update"}><Check size={17} /> اعمال روی انتخابی ها</Button>
            <Button variant="danger" onClick={bulkDelete} disabled={!selected.length} busy={busy === "bulk-delete"}><Trash2 size={17} /> حذف انتخابی ها</Button>
          </div>

          <div className="link-list">
            {filtered.map((config) => (
              <article className="link-row" key={config.id}>
                <input className="row-check" type="checkbox" checked={selected.includes(config.id)} onChange={(event) => setSelected((items) => event.target.checked ? [...items, config.id] : items.filter((id) => id !== config.id))} />
                <div className="row-main">
                  <div className="title-line">
                    <strong>{config.service_name || "-"}</strong>
                    <span>{config.volume_text}</span>
                    {config.source_panel_key && <i>{config.source_panel_key}</i>}
                  </div>
                  <p>{config.profile_title || "نام نمایشی اختصاصی ندارد"}</p>
                  <a href={config.public_url} target="_blank" rel="noreferrer">{config.public_url}</a>
                </div>
                <div className="row-meta">
                  <span>دستگاه: {config.device_count.toLocaleString("fa-IR")} / {config.device_limit || "نامحدود"}</span>
                  <span>دسته: {config.category_key}</span>
                </div>
                <div className="row-actions">
                  <button className="icon-button" title="کپی لینک" onClick={() => navigator.clipboard.writeText(config.public_url)}><Copy size={17} /></button>
                  <a className="icon-button" title="باز کردن" href={config.public_url} target="_blank" rel="noreferrer"><ExternalLink size={17} /></a>
                  <button className="icon-button" title="ویرایش" onClick={() => edit(config)}><Clipboard size={17} /></button>
                  <button className="icon-button" title="ریست دستگاه" onClick={() => action(config, "devices/reset")}><RotateCcw size={17} /></button>
                  <button className="icon-button danger-icon" title="Revoke" onClick={() => action(config, "revoke")}><RefreshCw size={17} /></button>
                  <button className="icon-button danger-icon" title="حذف" onClick={() => removeConfig(config)}><Trash2 size={17} /></button>
                </div>
              </article>
            ))}
            {!filtered.length && <div className="empty">موردی برای نمایش پیدا نشد.</div>}
          </div>
        </section>
      </section>

      {editing && (
        <div className="drawer-backdrop" role="presentation">
          <aside className="drawer">
            <header>
              <div><h2>ویرایش لینک</h2><p>{editing.service_name || editing.token}</p></div>
              <button className="icon-button" onClick={() => setEditing(null)}><X size={18} /></button>
            </header>
            <form onSubmit={saveEdit} className="form-grid single">
              <Field label="لینک اصلی سابسکریپشن" hint="اگر این مقدار را تغییر بدهید، لینک اصلی سابسکریپشن با ذخیره همین فرم جایگزین می‌شود و لینک اختصاصی مشتری ثابت می‌ماند."><input dir="ltr" value={String(draft.upstream_url)} onChange={(event) => setDraft({ ...draft, upstream_url: event.target.value })} /></Field>
              <Field label="نام سرویس"><input value={String(draft.service_name)} onChange={(event) => setDraft({ ...draft, service_name: event.target.value })} /></Field>
              <Field label="Username پنل"><input value={String(draft.panel_username)} onChange={(event) => setDraft({ ...draft, panel_username: event.target.value })} /></Field>
              <Field label="نام نمایشی داخل برنامه ها"><input value={String(draft.profile_title)} onChange={(event) => setDraft({ ...draft, profile_title: event.target.value })} /></Field>
              <Field label="محدودیت دستگاه"><input type="number" min="0" value={Number(draft.device_limit)} onChange={(event) => setDraft({ ...draft, device_limit: Number(event.target.value || 0) })} /></Field>
              <Field label="حجم گیگ"><input type="number" min="0" value={Number(draft.volume_gb)} onChange={(event) => setDraft({ ...draft, volume_gb: Number(event.target.value || 0) })} /></Field>
              <Field label="دسته بندی"><input value={String(draft.category_key)} onChange={(event) => setDraft({ ...draft, category_key: event.target.value })} /></Field>
              <Field label="کانال اختصاصی"><input value={String(draft.channel_handle)} onChange={(event) => setDraft({ ...draft, channel_handle: event.target.value })} /></Field>
              <Field label="بازنویسی آدرس سرورهای داخل کانفیگ" hint="این بخش لینک اصلی را عوض نمی‌کند؛ فقط دامنه یا IP داخل کانفیگ‌ها را با الگوی old-host=new-host جایگزین می‌کند."><textarea dir="ltr" value={String(draft.address_rewrites)} onChange={(event) => setDraft({ ...draft, address_rewrites: event.target.value })} /></Field>
              <div className="switch-row">
                {(["show_header", "show_config_preview", "info_proxies_enabled"] as const).map((key) => (
                  <label className="switch" key={key}>
                    <input type="checkbox" checked={Boolean(draft[key])} onChange={(event) => setDraft({ ...draft, [key]: event.target.checked })} />
                    <span>{key === "show_header" ? "هدر" : key === "show_config_preview" ? "کانفیگ ها" : "اطلاعاتی"}</span>
                  </label>
                ))}
              </div>
              <div className="actions"><Button type="submit" busy={busy === `edit-${editing.id}`}><Save size={17} /> ذخیره</Button></div>
            </form>
          </aside>
        </div>
      )}
    </main>
  );
}
