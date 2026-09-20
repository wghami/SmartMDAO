% ============================================================================
% wing_architecture.lp
%
% Chooses a spar material and a rib count for a wing.
%
% THIS FILE IS THE DISCIPLINE. A language model may have drafted it, but what
% runs is these rules - reviewed, diffed and version-controlled like any other
% engineering artifact. Nothing is generated at run time.
%
% SmartMDAO injects the facts declared in the RuleDiscipline as plain atoms:
%     mass_band(heavy).  certification(part23).
% No number crosses this boundary. A continuous variable has to go through a
% declared Bands first, which is what keeps the threshold reviewable.
% ============================================================================

material(aluminium; cfrp).
ribs(5; 8).

% Exactly one of each - the discrete choice gradient-based MDO cannot reach.
1 { spar(M) : material(M) } 1.
1 { rib_count(N) : ribs(N) } 1.

% --- Engineering rules, the part a human reviews -----------------------------

% Aluminium cannot carry a heavy airframe at this span.
:- spar(aluminium), mass_band(heavy).

% Part 23 does not justify the cost of the denser rib pitch.
:- rib_count(8), certification(part23).

% Part 25 requires the denser pitch, whatever it costs.
:- rib_count(5), certification(part25).

% CFRP is not yet certified to Part 25 on this airframe. Together with the
% aluminium rule above, this makes a heavy Part 25 wing UNSATISFIABLE - and
% that is a PROOF that no architecture here works, not a sentinel we invented.
:- spar(cfrp), certification(part25).

% --- Cost model --------------------------------------------------------------
% Integer weights, because ASP has no floats. The mapping from real cost to
% these integers is a hypothesis, and it belongs in the discretisation.

cost(aluminium, 1).  cost(cfrp, 3).
rib_cost(5, 1).      rib_cost(8, 2).

% --- Pinning (required; see docs/design/004) ---------------------------------

% 1. The real objective, at priority @1.
#minimize { C@1,M : spar(M), cost(M,C) ;
            D@1,N : rib_count(N), rib_cost(N,D) }.

% 2. A total tie-break at @0, ranking over a DISTINCT value per candidate.
%    `#minimize { 1@0,M : spar(M) }` would LOOK like a tie-break and separate
%    nothing - the weight is the same constant for every candidate.
rank(aluminium, 1).  rank(cfrp, 2).
rib_rank(5, 1).      rib_rank(8, 2).

#minimize { R@0,M : spar(M), rank(M,R) ;
            S@0,N : rib_count(N), rib_rank(N,S) }.

#show spar/1.
#show rib_count/1.
