// frontend/src/api.js
import axios from "axios";
// Use same origin API (relative path) so frontend and backend on same host will work on Replit
const API_BASE = ""; // empty = same origin

export async function getQuote(from, to, amount, criteria = ["best_landing","lowest_fees"], top_n = 3, weights = null) {
  const payload = { from_currency: from, to_currency: to, amount, criteria, top_n };
  if (weights) payload.weights = weights;
  const r = await axios.post(`${API_BASE}/quote`, payload);
  return r.data;
}
