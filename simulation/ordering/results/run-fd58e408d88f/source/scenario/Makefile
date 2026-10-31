CXX = g++
CXXFLAGS = -std=c++11 -O3 -DNOLOG -include cstring
INCLUDES = -I.. -I../algorithm -I../scheduler -I../topology -I../scan -I.
NATIVE = ../algorithm/matrix.cpp ../algorithm/all2all.cpp ../scheduler/config.cpp ../scheduler/local.cpp ../scheduler/global.cpp
SHARED = ../scan/planner.cpp ../topology/model.cpp ../topology/matching.cpp
.PHONY: check
build:
	mkdir -p build
build/planner_test: tests/planner_test.cc $(SHARED) $(NATIVE) ../scan/planner.h | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) $< $(SHARED) $(NATIVE) -o $@
check: build/planner_test
	build/planner_test
	python3 -m unittest discover -s tests -v
