import { useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { LuShieldCheck, LuEye, LuEyeOff, LuScanLine, LuCar, LuCheckCheck } from "react-icons/lu";
import { useAuth } from "../context/AuthContext";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    try {
      setLoading(true);
      await login(username, password);
      toast.success("Welcome back");
      navigate("/dashboard");
    } catch {
      setError("Invalid username or password. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-white">
      {/* Left — brand / illustration panel */}
      <div className="hidden lg:flex relative flex-col justify-between overflow-hidden bg-ink-950 p-12 text-white">
        <div
          className="absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage:
              "radial-gradient(circle at 1px 1px, white 1px, transparent 0)",
            backgroundSize: "28px 28px",
          }}
        />
        <div className="absolute -top-24 -right-24 h-96 w-96 rounded-full bg-brand-600/30 blur-3xl" />
        <div className="absolute bottom-0 -left-16 h-72 w-72 rounded-full bg-brand-500/20 blur-3xl" />

        <div className="relative flex items-center gap-3">
          <div className="h-11 w-11 rounded-xl bg-gradient-to-br from-brand-400 to-brand-700 grid place-items-center shadow-soft">
            <LuShieldCheck size={22} />
          </div>
          <div className="leading-tight">
            <p className="font-display font-semibold tracking-wide">CAMPUS</p>
            <p className="text-[11px] text-slate-400 tracking-[0.2em]">SECURITY SYSTEM</p>
          </div>
        </div>

        <div className="relative">
          <p className="text-4xl font-display font-bold leading-tight max-w-md">
            Automated plate recognition for every gate on campus.
          </p>
          <p className="text-slate-400 mt-4 max-w-md text-[15px]">
            YOLOv8 detection, EasyOCR reading, and live authorization checks —
            every entry and exit logged in real time.
          </p>

          <div className="mt-10 relative h-40 rounded-2xl border border-white/10 bg-white/5 backdrop-blur flex items-center px-6 gap-5 overflow-hidden">
            <div className="h-16 w-24 rounded-lg bg-gradient-to-br from-slate-200 to-slate-400 grid place-items-center shrink-0">
              <LuCar size={26} className="text-ink-900" />
            </div>
            <div className="flex-1">
              <p className="text-xs text-slate-400">Detected Vehicle</p>
              <p className="font-display font-bold text-lg tracking-wider">KA 25 AB 1234</p>
              <div className="flex items-center gap-1.5 mt-1.5 text-emerald-400 text-xs font-semibold">
                <LuCheckCheck size={14} /> Authorized · Gate 1
              </div>
            </div>
            <LuScanLine className="absolute right-4 top-4 text-brand-400/60" size={20} />
          </div>
        </div>

        <p className="relative text-xs text-slate-500">
          © {new Date().getFullYear()} Campus Security System · Vehicle Access Management
        </p>
      </div>

      {/* Right — form */}
      <div className="flex items-center justify-center p-6 sm:p-12">
        <form onSubmit={handleSubmit} className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-3 mb-8">
            <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-brand-400 to-brand-700 grid place-items-center">
              <LuShieldCheck className="text-white" size={20} />
            </div>
            <div className="leading-tight">
              <p className="font-display font-semibold text-ink-950">CAMPUS</p>
              <p className="text-[10px] text-slate-500 tracking-[0.2em]">SECURITY SYSTEM</p>
            </div>
          </div>

          <h1 className="text-2xl font-display font-bold text-ink-950">Welcome back</h1>
          <p className="text-slate-500 text-sm mt-1.5 mb-8">Sign in to access the security dashboard.</p>

          {error && (
            <div className="mb-4 px-3.5 py-2.5 rounded-lg bg-red-50 text-red-600 text-sm font-medium">
              {error}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5">Username</label>
              <input
                type="text"
                autoFocus
                placeholder="Enter your username"
                className="w-full rounded-lg border border-slate-200 px-3.5 py-3 text-sm focus-ring"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1.5">Password</label>
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  placeholder="Enter your password"
                  className="w-full rounded-lg border border-slate-200 px-3.5 py-3 pr-10 text-sm focus-ring"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((s) => !s)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  {showPassword ? <LuEyeOff size={18} /> : <LuEye size={18} />}
                </button>
              </div>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading || !username || !password}
            className="w-full mt-7 bg-brand-600 text-white py-3 rounded-lg font-semibold text-sm hover:bg-brand-700 disabled:opacity-60 transition-colors shadow-soft"
          >
            {loading ? "Signing in…" : "Sign In"}
          </button>

          <p className="text-center text-xs text-slate-400 mt-6">
            Access is restricted to authorized security personnel and administrators.
          </p>
        </form>
      </div>
    </div>
  );
}
