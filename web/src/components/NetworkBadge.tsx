import { getNetworkName, SubnetDef } from '../utils';

interface Props {
  ip: string | null | undefined;
  subnets: SubnetDef[];
}

export default function NetworkBadge({ ip, subnets }: Props) {
  const name = getNetworkName(ip, subnets);
  if (!name) return null;
  return (
    <span className="ml-1.5 text-[9px] font-bold px-1.5 py-0.5 rounded bg-accent2/15 text-accent2 uppercase tracking-wide whitespace-nowrap">
      {name}
    </span>
  );
}
