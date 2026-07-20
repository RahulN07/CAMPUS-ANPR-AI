import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import Modal from "./Modal";
import { TextField, SelectField } from "./FormField";
import { USER_ROLES } from "../utils/constants";
import { useReferenceData } from "../hooks/useReferenceData";
import { createUser, updateUser } from "../services/userService";

const EMPTY = {
  username: "",
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  role: "VIEWER",
  department: "",
  password: "",
};

export default function UserFormModal({ open, onClose, user, onSaved }) {
  const { departments } = useReferenceData();
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});

  useEffect(() => {
    if (user) {
      setForm({
        username: user.username || "",
        first_name: user.first_name || "",
        last_name: user.last_name || "",
        email: user.email || "",
        phone: user.phone || "",
        role: user.role || "VIEWER",
        department: user.department || "",
        password: "",
      });
    } else {
      setForm(EMPTY);
    }
    setErrors({});
  }, [user, open]);

  function update(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setErrors({});
    try {
      const payload = { ...form, department: form.department || null };
      if (user) {
        if (!payload.password) delete payload.password;
        await updateUser(user.id, payload);
        toast.success("User updated");
      } else {
        await createUser(payload);
        toast.success("User added");
      }
      onSaved?.();
      onClose();
    } catch (err) {
      const data = err?.response?.data;
      if (data && typeof data === "object") {
        setErrors(data);
        toast.error("Please fix the highlighted fields");
      } else {
        toast.error("Could not save user");
      }
    } finally {
      setSaving(false);
    }
  }

  const err = (key) => (Array.isArray(errors[key]) ? errors[key][0] : errors[key]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={user ? "Edit User" : "Add New User"}
      subtitle={user ? `Editing ${user.username}` : "Grant a new person access to the system"}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid sm:grid-cols-2 gap-4">
          <TextField
            label="Username"
            required
            disabled={!!user}
            value={form.username}
            onChange={(e) => update("username", e.target.value)}
          />
          {err("username") && <p className="text-xs text-red-500 -mt-2">{err("username")}</p>}
          <SelectField
            label="Role"
            required
            options={USER_ROLES}
            value={form.role}
            onChange={(e) => update("role", e.target.value)}
          />
          <TextField
            label="First Name"
            value={form.first_name}
            onChange={(e) => update("first_name", e.target.value)}
          />
          <TextField
            label="Last Name"
            value={form.last_name}
            onChange={(e) => update("last_name", e.target.value)}
          />
          <TextField
            label="Email"
            type="email"
            value={form.email}
            onChange={(e) => update("email", e.target.value)}
          />
          <TextField label="Phone" value={form.phone} onChange={(e) => update("phone", e.target.value)} />
          <SelectField
            label="Department"
            placeholder="Select department"
            options={departments.map((d) => ({ value: d.id, label: d.display_name || d.name }))}
            value={form.department}
            onChange={(e) => update("department", e.target.value)}
          />
          <TextField
            label={user ? "New Password" : "Password"}
            type="password"
            required={!user}
            placeholder={user ? "Leave blank to keep current" : ""}
            value={form.password}
            onChange={(e) => update("password", e.target.value)}
          />
          {err("password") && <p className="text-xs text-red-500 -mt-2">{err("password")}</p>}
        </div>

        <div className="flex justify-end gap-2 pt-4 border-t border-slate-100">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2.5 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="px-5 py-2.5 rounded-lg text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 disabled:opacity-60"
          >
            {saving ? "Saving…" : user ? "Save Changes" : "Add User"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
