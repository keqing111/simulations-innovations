CXX = g++
CXXFLAGS = -std=c++11 -O2 -DNOLOG
INCLUDES = -I.. -I../algorithm -I../scheduler -I.
NATIVE = ../algorithm/matrix.cpp ../algorithm/all2all.cpp ../scheduler/config.cpp ../scheduler/local.cpp ../scheduler/global.cpp
HEADERS = $(wildcard ../algorithm/*.h ../scheduler/*.h *.h) ../define.h ../test.h

.PHONY: all check
all: build/native_topology build/model_test build/matching_test

build:
	mkdir -p build

build/native_topology: main.cc model.cpp matching.cpp $(NATIVE) ../test.cpp $(HEADERS) | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) main.cc model.cpp matching.cpp $(NATIVE) ../test.cpp -o $@

build/model_test: tests/model_test.cc model.cpp model.h | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) tests/model_test.cc model.cpp -o $@

build/matching_test: tests/matching_test.cc model.cpp matching.cpp $(NATIVE) $(HEADERS) | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) tests/matching_test.cc model.cpp matching.cpp $(NATIVE) -o $@

check: all
	build/model_test
	build/matching_test
	python3 -m unittest discover -s tests -v
