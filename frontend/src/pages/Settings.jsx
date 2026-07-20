import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import {
  LuSettings2,
  LuScanLine,
  LuCamera,
  LuMail,
  LuDatabaseBackup,
  LuLock,
  LuServer,
} from "react-icons/lu";

import { getSystemSettings, updateSystemSettings } from "../services/referenceService";
import { TextField, SelectField } from "../components/FormField";
import { PageLoader } from "../components/Loader";

const TABS = [
  { key: "general", label: "General", icon: LuSettings2 },
  { key: "anpr", label: "ANPR Behavior", icon: LuScanLine },
  { key: "camera", label: "Camera Settings", icon: LuCamera },
  { key: "email", label: "Email Settings", icon: LuMail },
  { key: "backup", label: "Backup & Restore", icon: LuDatabaseBackup },
  { key: "security", label: "Security", icon: LuLock },
];

const INFRA_TABS = {
  camera: {
    title: "Camera Settings",
    body: "Gate cameras are configured per-gate under Access Management → Departments, and IP/stream details live on the Gate model in the backend. Add or edit gates from the Django admin at /admin/access_management/gate/.",
  },
  email: {
    title: "Email Settings",
    body: "Outbound email (for alert notifications) is configured via SMTP environment variables in the backend's .env file — this keeps credentials out of the database and the frontend bundle.",
  },
  backup: {
    title: "Backup & Restore",
    body: "Database backups are an infrastructure concern handled outside the app — e.g. a scheduled pg_dump / mysqldump job or your hosting provider's managed backups.",
  },
  security: {
    title: "Security",
    body: "Password policy, session lifetime, and JWT token expiry are configured in config/settings.py (SIMPLE_JWT) on the backend. Role-based permissions are enforced per-endpoint via DRF permission classes.",
  },
};

export default function Settings() {
  const [tab, setTab] = useState("general");
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      setLoading(true);
      const data = await getSystemSettings();
      setSettings(data);
    } catch (error) {
      console.error(error);
      toast.error("Couldn't load settings.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function update(field, value) {
    setSettings((s) => ({ ...s, [field]: value }));
  }

  async function handleSave() {
    try {
      setSaving(true);
      const data = await updateSystemSettings(settings);
      setSettings(data);
      toast.success("Settings saved.");
    } catch (error) {
      console.error(error);
      toast.error("Couldn't save settings.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <PageLoader label="Loading settings…" />;
  if (!settings) return null;

  return (
    <div className="grid lg:grid-cols-4 gap-6 animate-fadeIn">
      <div className="lg:col-span-1 bg-white rounded-2xl shadow-card border border-slate-100 p-2 h-fit">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                tab === t.key ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              <Icon size={16} /> {t.label}
            </button>
          );
        })}
      </div>

      <div className="lg:col-span-3 bg-white rounded-2xl shadow-card border border-slate-100 p-6">
        {tab === "general" && (
          <>
            <h3 className="font-display font-semibold text-ink-950 mb-1">General Settings</h3>
            <p className="text-sm text-slate-500 mb-6">Basic identity and formatting preferences for the system.</p>

            <div className="grid sm:grid-cols-2 gap-5">
              <TextField
                label="System Name"
                value={settings.system_name}
                onChange={(e) => update("system_name", e.target.value)}
              />
              <TextField
                label="Organization"
                value={settings.organization || ""}
                onChange={(e) => update("organization", e.target.value)}
                placeholder="Your college name"
              />
              <TextField
                label="Timezone"
                value={settings.timezone}
                onChange={(e) => update("timezone", e.target.value)}
              />
              <SelectField
                label="Date Format"
                value={settings.date_format}
                onChange={(e) => update("date_format", e.target.value)}
                options={[
                  { value: "DD MMM YYYY", label: "DD MMM YYYY (13 Jul 2026)" },
                  { value: "MM/DD/YYYY", label: "MM/DD/YYYY (07/13/2026)" },
                  { value: "DD/MM/YYYY", label: "DD/MM/YYYY (13/07/2026)" },
                ]}
              />
              <SelectField
                label="Time Format"
                value={settings.time_format}
                onChange={(e) => update("time_format", e.target.value)}
                options={[
                  { value: "12_HOUR", label: "12 Hour" },
                  { value: "24_HOUR", label: "24 Hour" },
                ]}
              />
              <TextField
                label="Items Per Page"
                type="number"
                min={5}
                max={100}
                value={settings.items_per_page}
                onChange={(e) => update("items_per_page", Number(e.target.value))}
              />
            </div>
          </>
        )}

        {tab === "anpr" && (
          <>
            <h3 className="font-display font-semibold text-ink-950 mb-1">ANPR Behavior</h3>
            <p className="text-sm text-slate-500 mb-6">
              Tune how the detection pipeline decides what counts as a confident read.
            </p>

            <div className="space-y-6 max-w-md">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-semibold text-slate-600">Confidence Threshold</label>
                  <span className="text-sm font-semibold text-brand-600">
                    {Math.round(settings.anpr_confidence_threshold * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={settings.anpr_confidence_threshold}
                  onChange={(e) => update("anpr_confidence_threshold", Number(e.target.value))}
                  className="w-full accent-brand-600"
                />
                <p className="text-[11px] text-slate-400 mt-1.5">
                  Detections below this OCR confidence are treated as unreadable rather than auto-matched.
                </p>
              </div>

              <label className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={settings.auto_flag_unauthorized}
                  onChange={(e) => update("auto_flag_unauthorized", e.target.checked)}
                  className="mt-1 h-4 w-4 rounded accent-brand-600"
                />
                <div>
                  <p className="text-sm font-medium text-ink-950">Auto-flag unauthorized vehicles</p>
                  <p className="text-xs text-slate-500">
                    Automatically create a notification for admins and security guards when an
                    unauthorized or expired plate is detected at any gate.
                  </p>
                </div>
              </label>
            </div>
          </>
        )}

        {INFRA_TABS[tab] && (
          <div className="max-w-lg">
            <div className="h-11 w-11 rounded-xl bg-slate-100 text-slate-500 grid place-items-center mb-4">
              <LuServer size={20} />
            </div>
            <h3 className="font-display font-semibold text-ink-950 mb-2">{INFRA_TABS[tab].title}</h3>
            <p className="text-sm text-slate-500 leading-relaxed">{INFRA_TABS[tab].body}</p>
          </div>
        )}

        {(tab === "general" || tab === "anpr") && (
          <div className="flex justify-end mt-8 pt-5 border-t border-slate-100">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-6 py-2.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium transition-colors disabled:opacity-60"
            >
              {saving ? "Saving…" : "Save Changes"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
