import { useParams, useNavigate } from "react-router-dom";
import RunbookDrawer from "../components/substrate/RunbookDrawer";

export default function SubstrateRunbookPage() {
  const { invariant } = useParams<{ invariant: string }>();
  const navigate = useNavigate();
  if (!invariant) return <p className="p-6 text-white/70">Missing invariant</p>;
  return (
    <div className="p-6">
      <RunbookDrawer
        invariant={invariant}
        onClose={() => navigate("/admin/substrate-health")}
      />
    </div>
  );
}
