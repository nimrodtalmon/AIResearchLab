Task: draft section `{section_id}` ({title}).

Abstract: "{abstract}"
Formal problem: "{formal_problem}"
Other section IDs in the paper: {section_titles}

Output a single JSON object with key `latex`. The LaTeX must start with
`\section{...}\label{sec:{section_id}}` and be ready for `pdflatex` without
extra preamble.
