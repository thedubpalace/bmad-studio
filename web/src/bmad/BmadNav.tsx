import { NavLink } from "react-router";

import { th } from "../i18n/th";

/** แถบย่อยของส่วน BMAD: ภาพรวม / Config / Skill (design.md IA ข้อ 4) */
export function BmadNav() {
  const items: Array<[string, string, boolean]> = [
    ["/bmad", th.bmadNav.overview, true],
    ["/bmad/config", th.bmadNav.config, false],
    ["/bmad/skills", th.bmadNav.skills, false],
  ];
  return (
    <nav className="bmad-nav" aria-label={th.bmadNav.label}>
      {items.map(([to, label, end]) => (
        <NavLink key={to} to={to} end={end} className="bmad-nav__link">
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
