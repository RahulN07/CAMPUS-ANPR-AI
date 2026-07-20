import { useEffect, useState } from "react";
import { listDepartments, listGates } from "../services/referenceService";

let cache = null;
let inflight = null;

async function loadAll() {
  if (cache) return cache;
  if (!inflight) {
    inflight = Promise.all([listDepartments(), listGates()]).then(
      ([deptRes, gateRes]) => {
        const departments = Array.isArray(deptRes) ? deptRes : deptRes.results || [];
        const gates = Array.isArray(gateRes) ? gateRes : gateRes.results || [];
        cache = { departments, gates };
        return cache;
      }
    );
  }
  return inflight;
}

export function useReferenceData() {
  const [data, setData] = useState(cache || { departments: [], gates: [] });
  const [loading, setLoading] = useState(!cache);

  useEffect(() => {
    let mounted = true;
    if (cache) return;
    loadAll()
      .then((res) => mounted && setData(res))
      .catch(() => {})
      .finally(() => mounted && setLoading(false));
    return () => {
      mounted = false;
    };
  }, []);

  return { ...data, loading };
}

export function departmentName(departments, id) {
  if (!id) return "—";
  const d = departments.find((dep) => dep.id === id);
  return d?.display_name || d?.name || `Dept #${id}`;
}

export function gateName(gates, id) {
  if (!id) return "—";
  const g = gates.find((gate) => gate.id === id);
  return g?.name || `Gate #${id}`;
}
