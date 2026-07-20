import { createContext, useContext, useEffect, useState } from "react";
import * as authService from "../services/authService";

const AuthContext = createContext();

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [initializing, setInitializing] = useState(true);

  useEffect(() => {
    loadUser();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadUser() {
    const token = localStorage.getItem("access");

    if (!token) {
      setInitializing(false);
      return;
    }

    try {
      const data = await authService.getCurrentUser();
      setUser(data);
    } catch (error) {
      console.log(error);
      logout();
    } finally {
      setInitializing(false);
    }
  }

  async function login(username, password) {
    const data = await authService.login(username, password);

    localStorage.setItem("access", data.access);
    localStorage.setItem("refresh", data.refresh);

    const me = await authService.getCurrentUser();

    setUser(me);
  }

  function logout() {
    localStorage.removeItem("access");
    localStorage.removeItem("refresh");
    setUser(null);
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        initializing,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
