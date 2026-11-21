PYTHON ?= python3
CXX ?= g++
CXXFLAGS := -O2 -std=c++17 -fno-fast-math -ffp-contract=off

.PHONY: all test
all: build/dependency_native.so build/dependency_partial.so build/dependency_prefix.so build/test_runtime

build:
	mkdir -p build

build/dependency_native.so: resource_order/dependency_native.cpp | build
	$(CXX) $(CXXFLAGS) -fPIC -shared $< -o $@

build/dependency_partial.so: resource_order/dependency_native.cpp | build
	$(CXX) $(CXXFLAGS) -fPIC -shared $< -o $@

build/dependency_prefix.so: resource_order/dependency_prefix.cpp | build
	$(CXX) $(CXXFLAGS) -fPIC -shared $< -o $@

build/test_runtime: integration/ns3/test_runtime.cc integration/ns3/NativeFastRuntime.h integration/ns3/HistoricalNativeFastRuntime.h | build
	$(CXX) $(CXXFLAGS) -Wall -Wextra -I integration/ns3 $< -o $@

test: all
	$(PYTHON) -m unittest discover -s tests -q
	./build/test_runtime
