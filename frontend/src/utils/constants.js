// Mirrors the choice enums defined on the Django models so the frontend
// never drifts from what the backend actually accepts.

export const OWNER_TYPES = [
  { value: "STUDENT", label: "Student" },
  { value: "FACULTY", label: "Faculty" },
  { value: "STAFF", label: "Staff" },
  { value: "CLERK", label: "Clerk" },
  { value: "VISITOR", label: "Visitor" },
];

export const VEHICLE_TYPES = [
  { value: "TWO_WHEELER", label: "Two Wheeler" },
  { value: "FOUR_WHEELER", label: "Four Wheeler" },
  { value: "HEAVY_VEHICLE", label: "Heavy Vehicle" },
];

export const FUEL_TYPES = [
  { value: "PETROL", label: "Petrol" },
  { value: "DIESEL", label: "Diesel" },
  { value: "ELECTRIC", label: "Electric" },
  { value: "CNG", label: "CNG" },
  { value: "HYBRID", label: "Hybrid" },
];

export const AUTHORIZATION_STATUSES = [
  { value: "AUTHORIZED", label: "Authorized" },
  { value: "UNAUTHORIZED", label: "Unauthorized" },
  { value: "EXPIRED", label: "Expired" },
  { value: "PENDING", label: "Pending Review" },
];

export const DIRECTIONS = [
  { value: "ENTRY", label: "Entry" },
  { value: "EXIT", label: "Exit" },
];

export const DETECTION_SOURCES = [
  { value: "WEBCAM", label: "Live Webcam" },
  { value: "UPLOAD", label: "Image Upload" },
  { value: "MANUAL", label: "Manual Entry" },
];

export const USER_ROLES = [
  { value: "ADMIN", label: "Admin" },
  { value: "SECURITY_GUARD", label: "Security Guard" },
  { value: "FACULTY", label: "Faculty" },
  { value: "VIEWER", label: "Viewer" },
];

// Static fallbacks only — real Department / Gate records (with their actual
// DB ids) are fetched from /api/access/departments/ and /api/access/gates/
// via ReferenceDataContext. These mirror the seed migration so dropdowns
// still render something sane if that request hasn't resolved yet.
export const DEPARTMENTS_FALLBACK = [
  { id: null, name: "CSE", display_name: "Computer Science Engineering" },
  { id: null, name: "CSD", display_name: "Computer Science & Design" },
  { id: null, name: "AI", display_name: "Artificial Intelligence" },
  { id: null, name: "ECE", display_name: "Electronics & Communication" },
  { id: null, name: "EEE", display_name: "Electrical & Electronics" },
  { id: null, name: "MECHANICAL", display_name: "Mechanical Engineering" },
  { id: null, name: "CIVIL", display_name: "Civil Engineering" },
  { id: null, name: "ADMINISTRATION", display_name: "Administration" },
  { id: null, name: "LIBRARY", display_name: "Library" },
];

export const labelFor = (list, value) =>
  list.find((item) => item.value === value)?.label ?? value ?? "—";

export const VEHICLE_COLORS = [
  { value: "Black", label: "Black" },
  { value: "White", label: "White" },
  { value: "Silver", label: "Silver" },
  { value: "Grey", label: "Grey" },
  { value: "Blue", label: "Blue" },
  { value: "Red", label: "Red" },
  { value: "Green", label: "Green" },
  { value: "Yellow", label: "Yellow" },
  { value: "Orange", label: "Orange" },
  { value: "Brown", label: "Brown" },
  { value: "Gold", label: "Gold" },
  { value: "Beige", label: "Beige" },
  { value: "Purple", label: "Purple" },
  { value: "Pink", label: "Pink" },
  { value: "Other", label: "Other" },
];