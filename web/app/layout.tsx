import type { Metadata } from "next";
import "./style.css";

export const metadata: Metadata = {
  title: "Salary management",
  description: "HR salary management workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
