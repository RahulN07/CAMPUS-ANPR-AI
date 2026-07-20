import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { PageLoader } from "../components/Loader";

export default function ProtectedRoute({ children }) {
  const { user, initializing } = useAuth();

  if (initializing) {
    return <PageLoader label="Loading…" />;
  }

  if (!user) {
    return <Navigate to="/" replace />;
  }

  return children;
}
