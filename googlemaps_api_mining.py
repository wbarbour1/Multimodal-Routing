from googlemaps_query_util import *
import googlemaps
import datetime as dt
import csv
import traceback
import cPickle
import os
from itertools import product, chain
import sys
import getopt
from inspect import getargspec
import multiprocessing
from copy import copy


class GooglemapsAPIMiner:
    """
    Full execution class to call Google Maps Python API (googlemaps) using an input query list and outputting results
        in the form of CSV and Python pickle objects.
    """
    def __init__(self, api_key_file, execute_in_time=False, split_transit=False, queries_per_second=10):
        """
        Initialize API miner with API key to the Google Maps service. Create empty class variables for reading input
            and executing queries.
        :param api_key_file: absolute or relative path to the text file storing the Google Maps API key
        :param execute_in_time: wait until the departure time indicated to execute the query as departure_time='now'
        :param queries_per_second: limit sent to googlemaps module (default = 10 q/s), can also be changed with delay
            parameter in run_queries() function
        :return: None
        """
        mykey = open(api_key_file, 'r').read()
        self.gmaps = googlemaps.Client(key=mykey, queries_per_second=queries_per_second)
        self.execute_in_time = execute_in_time
        self.split_transit = split_transit
        self.results = []
        self.queries = None
        self.input_header = None
        self.saved_input_filename = None
        return

    def read_input_queries(self, input_filename, verbose=False):
        """
        Reads CSV file with header to get list of queries. Some columns are direct and some are range parameters.
        See README.txt for more information on each and tips on formatting input documents.
        Direct parameters:
            - origin (required)
            - destination (required)
            - mode (required)
            - timezone (required)
            - waypoints     - traffic_model
            - alternatives  - avoid
            - units         - departure_time
            - arrival_time  - optimize_waypoints
            - transit_mode  - transit_routing_parameters
            - split_on_leg
        Range parameters:
            - arrival_time (arrival_time_min, arrival_time_max, arrival_time_delta)
            - departure_time (departure_time_min, departure_time_max, departure_time_delta)
            - origin [must be in lat/long] (origin_min, origin_max, origin_delta)
            - destination [must be in lat/long] (destination_min, destination_max, destination_delta]
        :param input_filename: absolute or relative path for input file (will be saved for possible use in output)
        :param verbose: T/F to print loaded queries
        :return: None
        """
        # Valid range parameters
        date_rp = (['arrival_time', 'departure_time'], ['_min', '_max', '_delta'])
        loc_rp = (['origin', 'destination'], ['_min', '_max', '_count', '_arrange'])

        with open(input_filename, 'rU') as f:
            input_reader = csv.reader(f, delimiter=',', quotechar='"')
            self.input_header = input_reader.next()
            # only accept valid execution parameters and the helper parameters (e.g., timezone)
            valid_args = getargspec(googlemaps.directions.directions)[0] + ['timezone', 'split_on_leg'] \
                         + [va[0] + va[1] for va in product(date_rp[0], date_rp[1])] \
                         + [va[0] + va[1] for va in product(loc_rp[0], loc_rp[1])]
            if not all([h in valid_args for h in self.input_header]):
                print "Invalid:", set(self.input_header).difference(set(valid_args))
                raise IOError("Header contains invalid columns/arguments.")
            self.queries = [{h: v for h, v in zip(self.input_header, row) if v is not None and v != ''}
                            for row in input_reader if not row[0].startswith('#')]
        # make sure all required parameters are present
        assert all(['origin' in q or 'origin_min' in q for q in self.queries]), \
            "Origin point must be supplied for all queries."
        assert all(['destination' in q or 'destination_min' in q for q in self.queries]), \
            "Destination point must be supplied for all queries."
        assert all(['mode' in q for q in self.queries]), "Mode must be supplied for all queries."
        assert all(['timezone' in q for q in self.queries]), "Timezone must be supplied for all queries."

        # Keep input filename in case output filename goes to default ('output_' + input filename).
        if os.path.split(input_filename)[0] == '':
            self.saved_input_filename = './' + input_filename
        else:
            self.saved_input_filename = input_filename

        # Parse out '|'-delimited waypoints, if supplied.
        for q in self.queries:
            if 'waypoints' in q:
                q['waypoints'] = q['waypoints'].split('|')

        # Track the list indices of queries with range parameters for removal later.
        remove_indices = []
        # Change range parameters to direct parameters.
        for r in date_rp[0]:
            rs = [r + suf for suf in date_rp[1]]
            for q in self.queries:
                if any([ri in q for ri in rs]):
                    # make sure all required range parameters are present
                    if not all([ri in q for ri in rs]):
                        print "Range param", r, "needs", date_rp[1]
                        print q
                        remove_indices.append(self.queries.index(q))
                        continue

                    try:
                        rmin = dt.datetime.strptime(q[r + '_min'], '%m/%d/%Y %H:%M')
                        rmax = dt.datetime.strptime(q[r + '_max'], '%m/%d/%Y %H:%M')
                        rdel = dt.timedelta(minutes=int(q[r + '_delta']))
                    except ValueError:
                        print "Problem with query format on a range parameter (couldn't convert to datetime)."
                        print q
                        remove_indices.append(self.queries.index(q))
                        continue
                    assert rmax > rmin, "Max time is not greater than min time."

                    i = 0
                    while rmin + i * rdel <= rmax:
                        qn = {}
                        for k, v in q.items():
                            if k not in [r] + rs:
                                qn[k] = v
                        qn[r] = rmin + i * rdel
                        self.queries.append(qn)
                        i += 1
                    remove_indices.append(self.queries.index(q))

                # now parse out non-range parameters
                elif r in q:
                    if not self.execute_in_time:
                        # wait until execution time to put datetime.now() in for 'now' if not executing in time
                        # doesn't matter because sorting doesn't occur when not executing in time
                        if type(q[r]) is str and q[r].lower() != 'now':
                            try:
                                q[r] = dt.datetime.strptime(q[r], '%m/%d/%Y %H:%M')
                            except ValueError:
                                print "Problem with query format on", r, "(couldn't convert to datetime)"
                                print q
                                remove_indices.append(self.queries.index(q))
                        elif type(q[r]) is str and q[r].lower() == 'now':
                            pass
                    else:
                        # if executing in time, then put datetime.now() in for the moment
                        # later it will get caught as slightly in the past and moved up to datetime.now() again
                        if type(q[r]) is str and q[r].lower() == 'now':
                            q[r] = dt.datetime.now()
                            # assign local timezone
                            q['timezone'] = mytz

        # Remove queries that contained range parameters.
        if remove_indices:
            self.queries = [self.queries[j] for j in range(len(self.queries)) if j not in remove_indices]
        remove_indices = []

        # add timezone to all datetime query parameters
        for q in self.queries:
            qtz = q['timezone']
            for r in date_rp[0] + [ri[0] + ri[1] for ri in product(date_rp[0], date_rp[1])]:
                if r in q and type(q[r]) is dt.datetime:
                    q[r] = convert_to_my_timezone(localize_to_query_timezone(time_in_query=q[r], timezone_in_query=qtz))
            q.__delitem__('timezone')

        for r in loc_rp[0]:
            rs = [r + suf for suf in loc_rp[1]]
            for q in self.queries:
                if any([ri in q for ri in rs]):
                    if not all([ri in q for ri in rs]):
                        print "Range param", r, "needs", loc_rp[1]
                        print q
                        remove_indices.append(self.queries.index(q))
                        continue
                    rmin = (float(q[r + '_min'].split(';')[0]), float(q[r + '_min'].split(';')[1]))
                    rmax = (float(q[r + '_max'].split(';')[0]), float(q[r + '_max'].split(';')[1]))
                    rdiv = (int(q[r + '_count'].split(';')[0]), int(q[r + '_count'].split(';')[1]))
                    rarr = q[r + '_arrange']
                    rdel = ((rmax[0] - rmin[0]) / (rdiv[0] - 1), (rmax[1] - rmin[1]) / (rdiv[1] - 1))
                    rvals = ([rmin[0] + i * rdel[0] for i in range(rdiv[0])],
                             [rmin[1] + i * rdel[1] for i in range(rdiv[1])])
                    if rarr == 'line':
                        rq = zip(rvals[0], rvals[1])
                    elif rarr == 'grid':
                        rq = [i for i in product(rvals[0], rvals[1])]
                    else:
                        print "Invalid arrangement argument. Use 'line' or 'grid'."
                        print q
                        remove_indices.append(self.queries.index(q))
                        continue
                    for rv in rq:
                        qn = {}
                        for k, v in q.items():
                            if k not in [r] + rs:
                                qn[k] = v
                        qn[r] = rv
                        self.queries.append(qn)
                    remove_indices.append(self.queries.index(q))
                else:
                    if ';' in q[r]:
                        try:
                            q[r] = tuple([float(i) for i in q[r].split(';')])
                        except ValueError:
                            print "Problem with query format on", r, "(';' included but couldn't convert to lat/long)"
                            print q
                            remove_indices.append(self.queries.index(q))
        # Remove queries that contained range parameters.
        if remove_indices:
            self.queries = [self.queries[j] for j in range(len(self.queries)) if j not in remove_indices]

        if verbose:
            for i in self.queries:
                print i

        # Redefine input header to remove columns that are unused or no longer needed (e.g., range parameters)
        # This will be used later in output files.
        self.input_header = list(set(chain(*[tuple(q.keys()) for q in self.queries])))
        # Sort queries for execution in order.
        if self.execute_in_time:
            assert not any(['arrival_time' in q for q in self.queries]), "Can't use arrival_time with execute_in_time."
            self.queries.sort(key=lambda x: x['departure_time'])
        print "Loaded", len(self.queries), "API queries."
        return

    def run_queries(self, verbose=False, here_are_the_queries=None):
        """
        Sequentially executes previously-loaded queries stored in class variable self.queries.
        :param verbose: runs recursive print (for legible indention) on each query result
        :param here_are_the_queries: list of queries for execution - will return results instead of storing
        :return: None
        """
        local_results = []
        if here_are_the_queries:
            queries = here_are_the_queries
        else:
            queries = self.queries

        successes = 0
        # run while loop with index, emulating for loop, so that items can be added to the list during looping
        # persistent sorting of list and nature of query additions ensures that additions happen after the current index
        qi = 0
        while qi < len(queries):
            q = queries[qi]
            # the query that gets executed can't have an invalid column
            qe = {k: v for k, v in q.items() if k != 'split_on_leg'}
            qi += 1
            if 'departure_time' in q:
                # q[tt] should have already been converted to dt.datetime unless it is 'now' (and execute_in_time False)
                if type(q['departure_time']) is str and q['departure_time'].lower() == 'now':
                    q['departure_time'] = localize_to_my_timezone(dt.datetime.now())
                # if 'now' was in departure_time and execute_in_time was True, then the parameter will be slightly in
                #   the past since it was put in as datetime.now() at the time of query input processing
                if q['departure_time'] < localize_to_my_timezone(dt.datetime.now() - dt.timedelta(minutes=10)):
                    raise AssertionError("Query departure time too far in past. Will tolerate up to 10 minutes.")
                elif q['departure_time'] < localize_to_my_timezone(dt.datetime.now()):
                    q['departure_time'] = localize_to_my_timezone(dt.datetime.now())
            # arrival time queries must be 90 minutes in the future (to allow for travel time)
            if 'arrival_time' in q:
                assert q['arrival_time'] > localize_to_my_timezone(dt.datetime.now() + dt.timedelta(hours=1.5))

            if self.execute_in_time:
                # All queries were checked that they were in the future during ingestion, but if queries multiple
                #   were given the same departure_time, then time.sleep() would be for negative number of seconds.
                # Therefore, just skip sleeping and execute at 'now'.
                print "Query departure_time:", q['departure_time']
                print "Now:", localize_to_my_timezone(dt.datetime.now())
                t = q['departure_time'] - localize_to_my_timezone(dt.datetime.now())
                if t > dt.timedelta(0):
                    print "Waiting for next query at", q['departure_time'].strftime("%m/%d/%Y %H:%M")
                    print "Sleep for", str(t).split('.')[0]
                    time.sleep(t.total_seconds())
                # Put in the exact current time for precision as indicated by googlemaps package documentation.
                q['departure_time'] = dt.datetime.now()
                print "Executing now (%s)." % q['departure_time'].strftime("%m/%d/%Y %H:%M")

            try:
                if 'id' in qe:
                    qid = qe['id']
                    del qe['id']
                else:
                    qid = None
                # execute full query and add result
                q_result = self.gmaps.directions(**qe)
                successes += 1
                if verbose:
                    recursive_print(q_result)
                    print '\n\n'
                if self.split_transit:
                    leg1_time = recursive_get(q_result, (0, 'legs', 0, 'duration_in_traffic', 'value')) / 60.
                    if leg1_time == 'n/a':
                        leg1_time = recursive_get(q_result, (0, 'legs', 0, 'duration', 'value')) / 60.
                    q_index = [True if 'id' in rq and rq['id'] == qid else False for rq in queries].index(True)
                    if 'departure_time' in queries[q_index]:
                        queries[q_index]['departure_time'] = qe['departure_time'] + dt.timedelta(minutes=leg1_time)
                    elif 'arrival_time' in queries[q_index]:
                        queries[q_index]['arrival_time'] = qe['arrival_time'] - dt.timedelta(minutes=leg1_time)
                    else:
                        pass
                # then get any applicable split queries
                if self.split_transit and 'split_on_leg' in q:
                    add_queries = self.build_intermediate_queries(full_query_to_split=q, result_to_split=q_result,
                                                                  id_stub=successes, verbose=verbose)
                    print add_queries
                    self.queries += list(chain(*add_queries))
                    # make sure all queries will get put in after the current one
                    assert all([aq1['departure_time'] > q['departure_time'] for aq1, aq2 in add_queries
                                if 'departure_time' in aq1 and aq1['departure_time'] is not None]), \
                        "Departure times need to be in future."
                    assert all([aq2['departure_time'] > q['departure_time'] for aq1, aq2 in add_queries
                                if 'departure_time' in aq2 and aq2['departure_time'] is not None]), \
                        "Departure times need to be in future."
                    if self.execute_in_time:
                        assert not any(['arrival_time' in aq for aq in add_queries]), \
                            "Arrival time not allowed for execute_in_time."
                    queries.sort(key=lambda x: x['departure_time'] if 'departure_time' in x else x['arrival_time'])
                    print queries
            except (googlemaps.exceptions.ApiError, googlemaps.exceptions.HTTPError,
                    googlemaps.exceptions.Timeout, googlemaps.exceptions.TransportError):
                traceback.print_exc()
                # append empty result to keep number of queries and number of results in sync
                q_result = []
            if here_are_the_queries:
                # save results in function instead of in class variable
                local_results.append(q_result)
            else:
                if self.split_transit and qid:
                    if qid in [i[0] for i in self.results]:
                        for res in self.results:
                            if res[0] == qid:
                                self.results[self.results.index(res)].append(q_result)
                    else:
                        self.results.append([qid, q_result])
                else:
                    self.results.append(q_result)
        print "Executed", successes, "queries successfully."
        if here_are_the_queries:
            return local_results
        else:
            return

    def output_results(self, output_filename=None, write_csv=True, write_pickle=True, get_outputs=None):
        """
        Write previously-gathered query results to file(s).
        :param output_filename: (optional) override 'output_' + input_filename for output files (no extension needed)
        :param write_csv: write output as CSV file (distance, duration, start(x, y), end(x, y))
        :param write_pickle: write results to pickle file, full query returns in list
        :param get_outputs: dict of column headers (keys) with tuples of the depth-wise calls to make to the list-dict
            results gathered; already defined within function, but these may not be valid depending on queries
        :return: None
        """
        # TODO: dump to file every 24 hours after minimum query time
        if not self.results:
            return
        if output_filename is None:
            output_stub = os.path.split(self.saved_input_filename)[0]
            output_fn = 'output_' + os.path.splitext(os.path.split(self.saved_input_filename)[-1])[0]
        else:
            if os.path.split(output_filename)[0] == '':
                output_stub = './'
            else:
                output_stub = os.path.split(output_filename)[0]
            output_fn = os.path.splitext(os.path.split(output_filename)[-1])[0]
        # TODO: if split_transit, then need specialized output format
        if write_pickle:
            try:
                with open(output_stub + '/' + output_fn + '.cpkl', 'wb') as f:
                    cPickle.dump(self.results, f)
            except:
                traceback.print_exc()
                print "Problem with output as pickle."
                print "Attempting to save as file './exception_dump.cpkl'."
                print "Rename that file to recover results, it may be overwritten if output fails again."
                with open("./exception_dump.cpkl", 'wb') as f:
                    cPickle.dump(self.results, f)
        if write_csv:
            try:
                # define output parameters and the appropriate depth-wise calls to list-dict combinations to get each
                if get_outputs:
                    outputs = get_outputs
                else:
                    outputs = {'distance': (0, 'legs', 0, 'distance', 'text'),
                               'duration': (0, 'legs', 0, 'duration', 'text'),
                               'duration-sec': (0, 'legs', 0, 'duration', 'value'),
                               'duration_in_traffic': (0, 'legs', 0, 'duration_in_traffic', 'text'),
                               'duration_in_traffic-sec': (0, 'legs', 0, 'duration_in_traffic', 'value'),
                               'start_x': (0, 'legs', 0, 'start_location', 'lng'),
                               'start_y': (0, 'legs', 0, 'start_location', 'lat'),
                               'end_x': (0, 'legs', 0, 'end_location', 'lng'),
                               'end_y': (0, 'legs', 0, 'end_location', 'lat')}
                with open(output_stub + '/' + output_fn + '.csv', 'w') as f:
                    writer = csv.writer(f, delimiter='|')
                    outputs_keys, outputs_values = zip(*outputs.items())
                    output_header = self.input_header + list(outputs_keys)
                    writer.writerow(output_header)
                    for q, res in zip(self.queries, self.results):
                        line = [q[ih] if ih in q else ''
                                for ih in self.input_header] + [recursive_get(res, oh)
                                                                for oh in outputs_values]
                        writer.writerow(line)
            except:
                traceback.print_exc()
                print "Problem with output as CSV."
                if not write_pickle:
                    print "Attempting to save results as pickle at './exception_dump.cpkl'."
                    print "Rename that file to recover results, it may be overwritten if output fails again."
                    with open("./exception_dump.cpkl", 'wb') as f:
                        cPickle.dump(self.results, f)
        return

    def run_pipeline(self, input_filename, output_filename=None, verbose_input=False, verbose_execute=False,
                     write_csv=True, write_pickle=True):
        """
        Executes read_input_queries(...), run_queries(...), and output_results(...) with their relevant parameters
        :param input_filename: absolute or relative path for input file (will be saved for possible use in output)
        :param output_filename: (optional) override 'output_' + input_filename for output files
        :param verbose_input: T/F to print loaded queries
        :param verbose_execute: T/F to print executed query results
        :param write_csv: write output as CSV file (distance, duration, start(x, y), end(x, y))
        :param write_pickle: write results to pickle file, full query returns in list
        :return: None
        """
        self.read_input_queries(input_filename=input_filename, verbose=verbose_input)
        self.run_queries(verbose=verbose_execute)
        self.output_results(output_filename=output_filename, write_csv=write_csv, write_pickle=write_pickle)
        return

    def build_intermediate_queries(self, full_query_to_split, result_to_split, id_stub, verbose=False):
        """
        Takes imported queries and executes the primary/full version of the query. Then builds secondary/split queries
            from the results, then to be executed in order and at correct times, if indicated.
        :param full_query_to_split:
        :param result_to_split:
        :param id_stub:
        :param verbose: runs recursive print for primary/full queries and prints information about intermediate stations
        :return: None
        """
        # find intermediate transit stations
        if full_query_to_split['split_on_leg'] == 'begin':
            steps = result_to_split[0]['legs'][0]['steps']
        elif full_query_to_split['split_on_leg'] == 'end':
            steps = list(result_to_split[0]['legs'][0]['steps'].__reversed__())
        else:
            raise ValueError("Don't know what leg to split on. Specify 'begin'/'end'.")
        if 'arrival_time' in full_query_to_split:
            align = 'arrival_time'
        elif 'departure_time' in full_query_to_split:
            align = 'departure_time'
        else:
            raise ValueError("Don't know if splitting queries on arrival or departure time.")
        # .index() will get first instance where the travel mode was 'TRANSIT'
        # if looking for end leg - the order was already reversed above
        leg = steps[[1 if st['travel_mode'] == 'TRANSIT' else 0 for st in steps].index(1)]
        stations = self.find_intermediate_transit_stations(transit_leg=leg)

        # build queries to and from intermediate transit stations
        secondary_queries = []
        for name, station in stations.items():
            sq = []
            if align == 'arrival_time':
                # cp1 will be second/final part of split trip
                cp1 = copy(full_query_to_split)
                del cp1['split_on_leg']
                cp1['id'] = "%04d%04d" % (id_stub, stations.index(station))
                # keep arrival time the same
                cp1['origin'] = station
                if full_query_to_split['split_on_leg'] == 'end':
                    cp1['mode'] = 'driving'
                sq.append(cp1)
                cp2 = copy(full_query_to_split)
                del cp2['split_on_leg']
                cp2['id'] = 0.
                cp2['arrival_time'] = dt.datetime.max
                cp2['destination'] = station
                if full_query_to_split['split_on_leg'] == 'end':
                    cp2['mode'] = 'driving'
                sq.append(cp2)
            else:
                # cp1 will be first part of split trip
                cp1 = copy(full_query_to_split)
                del cp1['split_on_leg']
                cp1['id'] = 0.
                # add time to the split query so that when it gets returned the sorting will place it later in the list
                cp1['departure_time'] = cp1['departure_time'] + dt.timedelta(minutes=3)
                # keep origin the same
                cp1['destination'] = station
                if full_query_to_split['split_on_leg'] == 'begin':
                    cp1['mode'] = 'driving'
                sq.append(cp1)
                cp2 = copy(full_query_to_split)
                del cp2['split_on_leg']
                cp2['id'] = 0.
                cp2['departure_time'] = dt.datetime.max
                cp2['origin'] = station
                # keep destination the same (second leg)
                if full_query_to_split['split_on_leg'] == 'end':
                    cp2['mode'] = 'driving'
                sq.append(cp2)
            secondary_queries.append(copy(sq))
            if verbose:
                print name, '\n', sq[0], '\n', sq[1]
        # return secondary queries so they can get added to query execution list, which will then get sorted
        return secondary_queries

    def find_intermediate_transit_stations(self, transit_leg, verbose=False):
        """

        :param transit_leg: portion of full transit trip for which to find intermediate stations
        :param verbose: print incremental results for debugging/information
        :return:
        """
        # distance limit in miles from stations found along route to the given route polyline
        dist_threshold = 0.1
        # find number of stations from query leg
        n_stations = transit_leg['transit_details']['num_stops']
        station_type = transit_leg['transit_details']['line']['vehicle']['type'].lower() + '_station'
        if verbose:
            print "n_stations:", n_stations
            print "station_type:", station_type
            print "raw polyline:", transit_leg['polyline']['points']
        # returned in list of (latitude, longitude) tuples
        pline = decode_polyline(transit_leg['polyline']['points'])
        if verbose:
            print "decoded polyline:", pline
        # arrange for n_stations across fractional length [0.0, 1.0] of polyline, then interpolate points
        linspace = [(j+1) * (1./n_stations) for j in range(n_stations-1)]
        # interpolation done in cartesian coordinates
        # also add in start and end points of polyline to get those stations as well
        #   they are listed first so those station names will be used to rule out erroneous top results
        interp = [pline[0], pline[-1]] + line_interpolate_points(points=pline, fracs=linspace)
        if verbose:
            print "interpolating points at:", linspace
            print "found interpolated points:", interp
        # gather coordinates of stations, keyed by station name
        intermed = {}
        for itp in interp:
            # run Google Maps places query to find top result for interpolated station location
            itm = self.gmaps.places(query='', location=itp, type=station_type)
            # interpolation method is not perfect because stations are not evenly spaced
            if itm['results'][0]['name'] not in intermed:
                top_result = itm['results'][0]
            elif len(itm['results']) > 1 and itm['results'][1]['name'] not in intermed:
                top_result = itm['results'][1]
                if verbose:
                    print "top result already found, using second result"
            top_result_point = (top_result['geometry']['location']['lat'], top_result['geometry']['location']['lng'])
            top_result_address = top_result['formatted_address'].replace(',', '')
            if verbose:
                print "interpolated point:", itp
                print "found point:", top_result_point
                print "name:", top_result['name']
                recursive_print(top_result)
            # calculate minimum cartesian distance from station found at interpolation point to polyline
            dist_to_pline = min([dist_to_segment(p1[1], p1[0], p2[1], p2[0], top_result_point[1], top_result_point[0])
                                 for p1, p2 in zip(pline[:-1], pline[1:])])
            if verbose:
                print "minimum distance from top result to polyline (approx miles):", (dist_to_pline * 55.)
            # make sure minimum distance is less than threshold (miles)
            # cartesian distance approximation for lat/lon is, for short distances, about 1/55 of haversine mileage
            if dist_to_pline > (dist_threshold / 55.):
                print "Distance from station found to polyline is approximately greater than %s miles." % dist_threshold
                print "Found minimum distance of approximately %s miles." % (dist_to_pline * 55.)
                print "Skipping this station -", top_result['name']
                continue
            intermed[top_result['name']] = top_result_address
        return intermed


def parallel_run_pipeline(all_args):
    """
    Wrapper method used in parallel execution to run class pipeline method.
    :param all_args: dictionary containing dictionaries of arguments for class initialization and pipeline function
    :return: "Success"/"Failure"
    """
    try:
        pipeline_args = all_args['pipeline_args']
        print "Process", os.getpid(), "pipeline_args:", pipeline_args
        init_args = all_args['init_args']
        print "Process", os.getpid(), "init_args:", init_args
        print "Beginning execution of input %s with PID %d." % (pipeline_args['input_filename'], os.getpid())
        GooglemapsAPIMiner(**init_args).run_pipeline(**pipeline_args)
        print "Execution of input %s with PID %d was successful." % (pipeline_args['input_filename'], os.getpid())
        return "Success"
    except KeyboardInterrupt:
        pass
    except BaseException as e:
        print "Exception raised on PID %d..." % os.getpid()
        print type(e), e.message
        return "Failure"


if __name__ == '__main__':
    # Set to True for running easily within IDE.
    if True:
        key_file = './will_googlemaps_api_key.txt'
        input_file = './test_queries.csv'
        g = GooglemapsAPIMiner(api_key_file=key_file, execute_in_time=False, split_transit=True)
        # g.read_input_queries(input_filename=input_file, verbose=True)
        # raise KeyboardInterrupt
        # g.run_pipeline(input_filename=input_file, verbose_input=True, verbose_execute=False)
        test_query = {'origin': 'Harvard transit station Boston MA', 'destination': 'Airport station Boston MA',
                      'mode': 'transit', 'departure_time': localize_to_my_timezone(dt.datetime.now()),
                      'split_on_leg': 'begin'}
        g.queries = [test_query]
        l = g.run_queries()
        print g.results
        sys.exit(0)

    usage = """
    usage: googlemaps_api_mining.py -k <api_key_file> -i <input_filename>
            --[execute_in_time, split_transit, queries_per_second, output_filename, write_csv, write_pickle,
                parallel_input_files, parallel_api_key_files]
    ex: python googlemaps_api_mining.py -k "./api_key.txt" -i "./test_queries.csv" --output_file "./output_test.csv"
    note: it is advised that the query input filenames be given as an absolute path
    note: using --parallel_input_files overrides output_filename and other parameters will be used for all tasks
    note: to check number of allowable parallel processes use... googlemaps_api_mining.py -c
    """
    # Collect command line arguments/options.
    command_line_arguments = sys.argv[1:]

    # Make default argument list for class initialization and pipeline execution.
    initargs = getargspec(GooglemapsAPIMiner.__init__)
    req = zip(initargs.args[1:-len(initargs.defaults)], [None]*len(initargs.args[1:-len(initargs.defaults)]))
    initspec = {k: v for k, v in req + zip(initargs.args[-len(initargs.defaults):], initargs.defaults)}
    rpargs = getargspec(GooglemapsAPIMiner.run_pipeline)
    req = zip(rpargs.args[1:-len(rpargs.defaults)], [None]*len(rpargs.args[1:-len(rpargs.defaults)]))
    rpspec = {k: v for k, v in req + zip(rpargs.args[-len(rpargs.defaults):], rpargs.defaults)}
    # Make space for additional input file names and API key file names for parallel execution.
    add_inputs = []
    add_keys = []

    try:
        opts, args = getopt.getopt(command_line_arguments, "hck:i:",
                                   ["execute_in_time=", "split_transit=", "queries_per_second=", "output_filename=",
                                    "write_csv=", "write_pickle=", "parallel_input_files=", "parallel_api_key_files="])
    except getopt.GetoptError:
        print usage
        sys.exit(2)

    for opt, arg in opts:
        # Information options.
        if opt == "-h":
            print usage
            sys.exit(0)
        elif opt == "-c":
            print "This system can safely execute %i mining processes in parallel." % multiprocessing.cpu_count()
            print "Provide one input file using -i and %i more with --parallel_input_files option, '|'-delimited." % \
                  (multiprocessing.cpu_count() - 1)
            sys.exit(0)

        # Initialization arguments.
        elif opt == "-k":
            initspec['api_key_file'] = arg
        elif opt == "--queries_per_second":
            initspec['queries_per_second'] = int(arg)
        elif opt == "--execute_in_time":
            if arg.lower() == 'true':
                initspec['execute_in_time'] = True
            elif arg.lower() == 'false':
                initspec['execute_in_time'] = False
            else:
                print "--execute_in_time should be [True/False/TRUE/FALSE/true/false]"
                sys.exit(2)
        elif opt == "--split_transit":
            if arg.lower() == 'true':
                initspec['split_transit'] = True
            elif arg.lower() == 'false':
                initspec['split_transit'] = False
            else:
                print "--split_transit should be [True/False/TRUE/FALSE/true/false]"
                sys.exit(2)

        # Pipeline arguments.
        elif opt == "-i":
            rpspec['input_filename'] = arg
        elif opt == "--output_filename":
            rpspec['output_filename'] = arg
        elif opt == "--write_csv":
            if arg.lower() == 'true':
                rpspec['write_csv'] = True
            elif arg.lower() == 'false':
                rpspec['write_csv'] = False
            else:
                print "--write_csv should be [True/False/TRUE/FALSE/true/false]"
                sys.exit(2)
        elif opt == "--write_pickle":
            if arg.lower() == 'true':
                rpspec['write_pickle'] = True
            elif arg.lower() == 'false':
                rpspec['write_pickle'] = False
            else:
                print "--write_pickle should be [True/False/TRUE/FALSE/true/false]"
                sys.exit(2)

        # Parallel execution arguments.
        elif opt == "--parallel_input_files":
            add_inputs = arg.split('|')
            # With the addition of the other input supplied by -i, the input file count must not exceed CPU cores.
            assert (len(add_inputs) + 1) <= multiprocessing.cpu_count(), \
                "Exceeded number of allowable processes. Use -c for info on processor availability."
        elif opt == "--parallel_api_key_files":
            add_keys = arg.split('|')

    if add_inputs:
        # Assemble full list of inputs. Then 'input_filename' in rpspec can be overwritten.
        add_inputs.append(copy(rpspec['input_filename']))
        # Assemble full list of API keys. Then 'api_key_file' in initspec can be overwritten.
        # Primary API key provided in argument '-k', which will be the API key to be used more than once.
        if add_keys:
            add_keys.append(copy(initspec['api_key_file']))
            print "Loaded additional API keys for a total of", len(add_keys), "keys."
            if len(add_keys) < len(add_inputs):
                print "Fewer keys than input files. Primary will be used", len(add_inputs) - len(add_keys) + 1, "times."
        print "Full filename list:"
        for fn in add_inputs:
            print '\t', fn
        # Need copies of pipeline argument spec with appropriate input file names.
        pipes = []
        inits = []
        for ai_i in range(len(add_inputs)):
            rpspec['input_filename'] = add_inputs[ai_i]
            # The minimum function will use API keys in order they were provided in 'parallel_api_key_files' followed
            #   by the primary API key in option '-k', the latter of which will be used multiple times if necessary.
            initspec['api_key_file'] = add_keys[min(ai_i, len(add_keys) - 1)]
            # Duplicate initspec filled with one of the input file names and API key file names.
            pipes.append(copy(rpspec))
            inits.append(copy(initspec))
        print "Built list of", len(pipes), "argument specs for pipeline execution."

        pool = multiprocessing.Pool(multiprocessing.cpu_count())
        print "Assembled pool of", multiprocessing.cpu_count(), "processes."
        p = pool.map_async(parallel_run_pipeline, [{'init_args': i, 'pipeline_args': p} for i, p in zip(inits, pipes)])
        try:
            # Timeout set at approximately 12 days.
            results = p.get(0xFFFFF)
        except KeyboardInterrupt:
            print 'Multiprocessing got KeyboardInterrupt. Terminating...'
            sys.exit(1)

        print "Parallel execution results:"
        for r in results:
            print r
    else:
        g = GooglemapsAPIMiner(**initspec)
        g.run_pipeline(**rpspec)
