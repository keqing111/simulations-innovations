# Fresh final review package — native paper reproduction

Read PLAN.md,PROGRESS.md,REPORT.md;reproduce.py,reference.py,driver.cc,link_profile.cc/.h,tests/*.py under /home/julius/fast/FAST/simulation/reproduction. New files only; DO NOT conflate original dirty simulation/topology hooks with compiled pristine source. Build extracts assets/upstream.tar from pinnedGit0712d29…(identicalremote), validates knownSHA andcompiles unmodified authorcpp,configfactorysymbolrename only. OfficialPDFassets/paper.pdf isprimarypublication,notoldv1.

User: completely reproduce originalFAST/paper before optimizations. Scope: possibleFig17 simulation; local1RTX5060 makesactual4serverGPUexperimentsunavailable,explicitlyreport. Missingalpha/seeds/Fig17babsoluteparams disclosed, nofitting. Full981casegrid not run pending review; real2caseCLI smoke and6Python tests ready. One reviewer only,readonly,nochildren/noedits. You may run checks in temporary paths. Do notlaunchfullgrid oroverwriteassets/workingtree.

Review focus verbatim:
- Staged sources byte-for-byte from pinnedgitblob, not dirty files; wrapper compiler-only configuration changes disclosed.
- MB/GB vsMiB/Gbps units,perGPU-pair vsperGPUtotal; no bandwidth-factor8 mistakes or totaloverflow.
- PDFcurve extraction/calibration/clipping cannot fabricate points; each curve bound toactualPDFhash.
- Rawpredictedtime vsCPUtime distinct; no choice of timing scope tofit; repeats/seeds not confidence over hardware variability.
- Failed/incomplete/unregisteredruns,stale source/PDF/binary,changedparams/matrices reject; standalonearchive rebuild possible.

Return Critical/Important/Minor with concrete repros/locations;list declined-to-judge items. Rulings inPROGRESS.md. Original sourcearchive includes known uninitializedcustomconstructor;runner deliberately usesenumconstructor. Nooriginalcorefixplanned. OneRED→GREENfixpass,no rereview. Formal results and reportfigures pending intentionally until after review; judgecode/scientificcontracts,notunobservedperf.
