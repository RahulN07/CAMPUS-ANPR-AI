import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function AdminRoute({ children }) {
  const { user } = useAuth();

  if (!user) return null; // ProtectedRoute above handles the redirect
  if (user.role !== "ADMIN") return <Navigate to="/dashboard" replace />;

  return children;
}
