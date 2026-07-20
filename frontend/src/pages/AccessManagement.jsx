import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  LuUsers,
  LuUserCheck,
  LuUserX,
  LuBuilding2,
  LuPlus,
  LuPencil,
  LuTrash2,
  LuSearch,
} from "react-icons/lu";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import ConfirmDialog from "../components/ConfirmDialog";
import UserFormModal from "../components/UserFormModal";
import EmptyState from "../components/EmptyState";
import { SkeletonRow } from "../components/Loader";
import { listUsers, deleteUser } from "../services/userService";
import { listVehicles } from "../services/vehicleService";
import { useReferenceData, departmentName } from "../hooks/useReferenceData";
import { initials } from "../utils/format";
import { USER_ROLES, labelFor } from "../utils/constants";

export default function AccessManagement() {
  const { departments } = useReferenceData();
  const [tab, setTab] = useState("users");
  const [users, setUsers] = useState([]);
  const [vehicles, setVehicles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [toDelete, setToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [usersData, vehiclesData] = await Promise.all([
        listUsers({ page_size: 500 }),
        listVehicles({ page_size: 500 }).catch(() => []),
      ]);
      setUsers(Array.isArray(usersData) ? usersData : usersData.results || []);
      setVehicles(Array.isArray(vehiclesData) ? vehiclesData : vehiclesData.results || []);
    } catch {
      toast.error("Could not load access management data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const filteredUsers = useMemo(() => {
    if (!search) return users;
    const q = search.toLowerCase();
    return users.filter(
      (u) =>
        u.username?.toLowerCase().includes(q) ||
        u.email?.toLowerCase().includes(q) ||
        `${u.first_name} ${u.last_name}`.toLowerCase().includes(q)
    );
  }, [users, search]);

  const activeCount = users.filter((u) => u.is_active).length;
  const inactiveCount = users.length - activeCount;

  async function confirmDelete() {
    setDeleting(true);
    try {
      await deleteUser(toDelete.id);
      toast.success("User removed");
      setToDelete(null);
      load();
    } catch {
      toast.error("Could not delete user");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-5 animate-fadeIn">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard title="Total Users" value={users.length} icon={LuUsers} tone="blue" />
        <StatCard title="Active" value={activeCount} icon={LuUserCheck} tone="green" />
        <StatCard title="Inactive" value={inactiveCount} icon={LuUserX} tone="red" />
        <StatCard title="Departments" value={departments.length} icon={LuBuilding2} tone="purple" />
      </div>

      <div className="bg-white rounded-2xl shadow-card border border-slate-100">
        <div className="flex items-center justify-between px-5 pt-4">
          <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
            <button
              onClick={() => setTab("users")}
              className={`px-4 py-1.5 rounded-md text-sm font-semibold transition-colors ${
                tab === "users" ? "bg-white shadow-soft text-ink-950" : "text-slate-500"
              }`}
            >
              Users
            </button>
            <button
              onClick={() => setTab("departments")}
              className={`px-4 py-1.5 rounded-md text-sm font-semibold transition-colors ${
                tab === "departments" ? "bg-white shadow-soft text-ink-950" : "text-slate-500"
              }`}
            >
              Departments
            </button>
          </div>

          {tab === "users" && (
            <button
              onClick={() => {
                setEditing(null);
                setFormOpen(true);
              }}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700"
            >
              <LuPlus size={16} /> Add User
            </button>
          )}
        </div>

        {tab === "users" && (
          <div className="p-5 space-y-4">
            <div className="relative max-w-sm">
              <LuSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search users…"
                className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-200 text-sm focus-ring"
              />
            </div>

            <div className="overflow-x-auto rounded-xl border border-slate-100">
              <table className="w-full text-sm">
                <thead className="bg-slate-50/70">
                  <tr className="text-left text-slate-500 text-xs uppercase tracking-wide">
                    <th className="px-4 py-3 font-semibold">Name</th>
                    <th className="px-4 py-3 font-semibold">Email</th>
                    <th className="px-4 py-3 font-semibold">Department</th>
                    <th className="px-4 py-3 font-semibold">Role</th>
                    <th className="px-4 py-3 font-semibold">Status</th>
                    <th className="px-4 py-3 font-semibold text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {loading && Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} cols={6} />)}

                  {!loading &&
                    filteredUsers.map((u) => (
                      <tr key={u.id} className="table-row-hover">
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            <div className="h-9 w-9 rounded-full bg-gradient-to-br from-brand-400 to-brand-700 text-white grid place-items-center text-xs font-semibold shrink-0">
                              {initials(
                                u.first_name || u.last_name
                                  ? `${u.first_name} ${u.last_name}`
                                  : u.username
                              )}
                            </div>
                            <div className="min-w-0">
                              <p className="font-medium text-ink-950 truncate">
                                {u.first_name || u.last_name ? `${u.first_name} ${u.last_name}` : u.username}
                              </p>
                              <p className="text-xs text-slate-400">@{u.username}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-slate-600">{u.email || "—"}</td>
                        <td className="px-4 py-3 text-slate-600">{departmentName(departments, u.department)}</td>
                        <td className="px-4 py-3 text-slate-600">{labelFor(USER_ROLES, u.role)}</td>
                        <td className="px-4 py-3">
                          <StatusBadge status={u.is_active ? "ACTIVE" : "INACTIVE"} />
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center justify-end gap-1">
                            <button
                              onClick={() => {
                                setEditing(u);
                                setFormOpen(true);
                              }}
                              className="h-8 w-8 grid place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-brand-600"
                            >
                              <LuPencil size={16} />
                            </button>
                            <button
                              onClick={() => setToDelete(u)}
                              className="h-8 w-8 grid place-items-center rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-500"
                            >
                              <LuTrash2 size={16} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>

              {!loading && filteredUsers.length === 0 && (
                <EmptyState icon={LuUsers} title="No users found" message="Try a different search, or add a new user." />
              )}
            </div>
          </div>
        )}

        {tab === "departments" && (
          <div className="p-5 grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {departments.map((dept) => {
              const deptUsers = users.filter((u) => u.department === dept.id).length;
              const deptVehicles = vehicles.filter((v) => v.department === dept.id).length;
              return (
                <div key={dept.id} className="rounded-xl border border-slate-100 p-4">
                  <div className="flex items-center gap-3 mb-3">
                    <div className="h-10 w-10 rounded-lg bg-brand-50 text-brand-600 grid place-items-center font-display font-bold text-sm shrink-0">
                      {dept.name.slice(0, 2)}
                    </div>
                    <p className="font-semibold text-ink-950 text-sm leading-tight">{dept.display_name || dept.name}</p>
                  </div>
                  <div className="flex items-center gap-4 text-xs text-slate-500">
                    <span>{deptUsers} users</span>
                    <span>{deptVehicles} vehicles</span>
                  </div>
                </div>
              );
            })}
            {departments.length === 0 && (
              <EmptyState icon={LuBuilding2} title="No departments configured" />
            )}
          </div>
        )}
      </div>

      <UserFormModal open={formOpen} user={editing} onClose={() => setFormOpen(false)} onSaved={load} />

      <ConfirmDialog
        open={!!toDelete}
        title="Remove this user?"
        message={toDelete ? `${toDelete.username} will lose access to the system.` : ""}
        loading={deleting}
        onCancel={() => setToDelete(null)}
        onConfirm={confirmDelete}
      />
    </div>
  );
}
