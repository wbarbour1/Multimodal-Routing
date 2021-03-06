README.txt for googlemaps_api_miner.py
Language: Python 2.7
Author: William Barbour
Date: 02/22/2017
Revised: 03/06/2017

This document describes the process of mining the Google Maps Directions API using the Python API package, googlemaps.
The documentation for the API package is here - https://googlemaps.github.io/google-maps-services-python/docs/2.4.5/
Some sections of this README are taken from that documentation.
Two Python files are used for mining: a class file and a utility function file.
The overall process is as follows (3 component functions):
    - take input in the form of a CSV file, detailing the queries to be executed on the Google Maps Directions API
        read_input_queries(input_filename, verbose=False)
    - sequentially execute queries and store results
        run_queries()
    - output results of queries in CSV and/or Python pickled object
        output_results(output_filename=None, write_csv=True, write_pickle=True, get_outputs=None)
These three functions are wrapped into a pipeline function run_pipeline(...) with arguments:
    input_filename,
    output_filename=None,
    verbose=False,
    write_csv=True,
    write_pickle=True
The pipeline function may also be executed from the command line with the following usage:
    usage: python googlemaps_api_mining.py -k <api_key_file> -i <input_file>
            --[execute_in_time, queries_per_second, split_transit, output_filename, write_csv, write_pickle,
                parallel_input_files, parallel_api_key_files]
    example: python googlemaps_api_mining.py -k "./api_key.txt" -i "./test_queries.csv"
                --output_file "./output_test.csv" --write_csv True --write_pickle False
    note: using --parallel_input_files overrides output_filename and other parameters will be used for all tasks
    note: to check number of allowable parallel processes use... googlemaps_api_mining.py -c
This package also provides the ability to execute queries on the API at the time for which they are indicated in the
    future. This functionality is useful in acquiring real time data, which is more accurate than the predicted values
    that Google will provide. Use the execute_in_time option in the class __init__ or the command line call.
After the implementation of execute_in_time option, it was observed that a method was needed to run multiple of these
    query batches simultaneously. Since the process relies heavily on waiting appropriate amounts of time, the processes
    needed to be spread across functional CPU cores and not pooled as threads (see Global Interpreter Lock). This mining
    program uses Python's Multiprocessing library and asynchronous pooling to achieve this. To use, perform a command
    line call with one input via -i and additional inputs via --parallel_input_files ("-enclosed string of |-delimited
    file names). Also use the -c option (with no others) for information of the current systems parallel capabilities.
Numerous API keys may be used by providing the file paths to each key (one per file) in the --parallel_api_key_files
    option ("-enclosed string of |-delimited file names).


INPUT:
------------------
The GooglemapsAPIMiner class will load an input query file using its read_input_queries(filename, verbose=False)
    function. This file can be assembled using Microsoft Excel and saved as a CSV file. An example file and the
    corresponding is included for reference. Ensure that no cells contain commas, as Excel saves CSV files with comma
    delimiters. Additionally, the entire sheet may need to be formatted as text in order to avoid the correction of
    exact date strings into Excel's date format. The guidelines for the file format are as follows:

The header (first) row of the file defines the parameters (columns) of the queries. Including a parameter does not mean
    that every query must define a value for it. Therefore, it is fine to use a header line with all available
    parameters, and define only the needed ones. That being said, there are three required parameters that every
    query must define: origin, destination, and mode. Parameters fall into two types: direct and range parameters.
    Range parameters are used to generate a series of queries using the range of values specified; they override their
    corresponding direct parameters if supplied. Multiple range parameters may be combined per line. Quoting is not
    necessary; all parameters loaded as strings and converted as necessary.

    Direct parameters:
    (required...)
    > origin (string, ;-separated floats) – address or lat/long value from which you wish to calculate directions
    > destination (string, ;-separated floats) – address or lat/long value to which you wish to calculate directions
    > mode (string) – mode of transport for calculating directions [driving, walking, bicycling, transit]
    > timezone (string) - timezone corresponding to the departure or arrival time (e.g., pacific, eastern, etc.)
        for timezones outside of continental US, refer to pytz.common_timezones for list of values
    (optional...)
    > departure_time (string format "MM/DD/YYYY hh:mm" or "now") – desired time of departure (must be now or in future)
    > arrival_time (string format "MM/DD/YYYY hh:mm" or "now") – desired time of arrival (must be now or in future)
        Note: may not specify both departure_time and arrival_time
    > split_on_leg (string, [begin, end] accepted) - specifies whether to split (if indicated) transit trip such that
        the driving option is explored on the first transit leg or the final transit leg of the route
    > waypoints (string or ;-separated floats, |-delimited if multiple) –
        specifies an array of waypoints which alter a route by calculating it through the specified location(s)
    > alternatives (bool) – if True, more than one route may be returned in response [true, false, t, f, TRUE, FALSE]
    > avoid (list or string) – indicates that the calculated route(s) should avoid the indicated features
        [tolls, highways, ferries, indoor]
    > units (string) – Specifies the unit system to use when displaying results [metric, imperial]
    > optimize_waypoints (bool) – optimize route by reordering waypoints [true, false, t, f, TRUE, FALSE]
    > transit_mode (string, ;-delimited strings) – one or more preferred modes of transit ('mode' must be 'transit')
        [bus, subway, train, tram, rail], “rail” is equivalent to [train, tram, subway].
    > transit_routing_preference (string) – preferences for transit requests [less_walking, fewer_transfers]
    > traffic_model – predictive travel time model to use ('mode' must be 'driving' and 'departure_time' defined)
        [best_guess, optimistic, pessimistic]

    Range parameters:
    > arrival_time - specify arrival_time_min, arrival_time_max, arrival_time_delta
        >> arrival_time_min (string format "MM/DD/YYYY hh:mm")
        >> arrival_time_max (string format "MM/DD/YYYY hh:mm")
        >> arrival_time_delta (integer number of minutes)
    > departure_time (departure_time_min, departure_time_max, departure_time_delta)
        >> departure_time_min (string format "MM/DD/YYYY hh:mm")
        >> departure_time_max (string format "MM/DD/YYYY hh:mm")
        >> departure_time_delta (integer number of minutes)
    > origin [must be lat/long] (origin_min, origin_max, origin_divs, origin_arrange)
        >> origin_min - ;-separated floats for lat/long
        >> origin_max - ;-separated floats for lat/long (defines coordinates of opposite corner of grid/line)
        >> origin_count - ;-separated integers for number of points including _min and _max to generate on lat/long
        >> origin_arrange - [line, grid] for incrementing lat/long simultaneously (line) or independently (grid)
    > destination [must be in lat/long] (destination_min, destination_max, destination_divs, destination_arrange)
        >> destination_min - ;-separated floats for lat/long
        >> destination_max - ;-separated floats for lat/long
        >> destination_count - ;-separated integers for number of points including _min and _max to generate on lat/long
        >> destination_arrange - [line, grid] for incrementing lat/long simultaneously (line) or independently (grid)


OUTPUT:
------------------
Output from the API queries is provided according to the documentation of the API at:
    https://developers.google.com/maps/documentation/directions/intro#DirectionsResponses
    The response is translated to a python object from the JSON response such that it contains lists and dicts. Each
    query result is appended to a class-wide storage that will be used to dump all results to output files at once.
    Two output options are available:

    Pickle:
    - stores results in full in their object form so that they may be mined in more detail later

    CSV:
    - keeps query values and header (only direct parameters used in a query are kept)
    - uses pipe ('|') delimiter
    - looks at each result and gathers basic values
        - by default: distance (meters), duration (seconds), start (X, Y) (longitude/latitude), end (X, Y)
    - can be supplied with 'get_outputs' to output different values from the results
        (format is dict{ column_names: tuple(depth-wise calls to make to each query result lists/dicts to get value)}