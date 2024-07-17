from time import sleep

from web3.eth import Eth
from web3.exceptions import ContractLogicError
from web3.types import FilterParams, LogReceipt


def exponential_retry(func_name: str = None):
    def wrapper(func):
        def inner(*args, no_retry=False, **kwargs):
            if no_retry:
                return func(*args, **kwargs)

            retries = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if isinstance(e, ContractLogicError):
                        raise
                    if retries == 0:
                        wait_for = 0
                    elif retries < 6:
                        wait_for = 2 ** (retries - 1)
                    else:
                        wait_for = 30
                    print(f"Web3Advanced.eth.{func_name or func.__name__} threw \"{repr(e)}\" on {retries+1}th try, retrying in {wait_for}s")

                    retries += 1
                    sleep(wait_for)
        return inner
    return wrapper


class EthAdvanced(Eth):
    # todo: implement multicall
    METHODS_TO_RETRY = [
        'fee_history', 'call', 'create_access_list', 'estimate_gas',
        'get_transaction', 'get_raw_transaction', 'get_raw_transaction_by_block',
        'send_transaction', 'send_raw_transaction', 'get_block', 'get_balance',
        'get_code', 'get_transaction_count', 'get_transaction_receipt',
        'wait_for_transaction_receipt', 'get_storage_at', 'replace_transaction',
        'modify_transaction', 'sign', 'sign_transaction', 'sign_typed_data', 'filter',
        'get_filter_changes', 'get_filter_logs', 'uninstall_filter', 'submit_hashrate',
        'get_work', 'submit_work',
    ]

    PROPERTIES_TO_RETRY = [
        'accounts', 'hashrate', 'block_number', 'chain_id', 'coinbase', 'gas_price',
        'max_priority_fee', 'mining', 'syncing'
    ]

    FILTER_RANGES_TO_TRY = sorted([
        10_000,
        5_000,
        2_000,
        1_000,
        500,
        200,
        100,
        50,
        20,
        10,
        5,
        2,
        1
    ], reverse=True)
    assert FILTER_RANGES_TO_TRY[-1] == 1

    def __init__(self, w3):
        super().__init__(w3=w3)

        if self.w3.should_retry:
            self._wrap_methods_with_retry()

        self.filter_block_range = self._find_max_filter_range()

    def _wrap_methods_with_retry(self):
        for method_name in self.METHODS_TO_RETRY:
            method = getattr(self, method_name)
            setattr(self, method_name, exponential_retry(func_name=method_name)(method))

        for prop_name in self.PROPERTIES_TO_RETRY:
            prop = getattr(self.__class__, prop_name)
            wrapped_prop = property(exponential_retry(func_name=prop_name)(prop.fget))
            setattr(self.__class__, prop_name, wrapped_prop)

    def get_logs(self, filter_params: FilterParams, show_progress_bar=False, p_bar=None) -> list[LogReceipt]:
        # getting the respective block numbers, could be block hashes or strings like "latest"
        from_block = filter_params["fromBlock"]
        to_block = filter_params["toBlock"]
        if not isinstance(from_block, int):
            from_block = self.get_block(from_block)["number"]
        if not isinstance(to_block, int):
            to_block = self.get_block(to_block)["number"]

        # note: fromBlock and toBlock are both inclusive. e.g. 5 to 6 are 2 blocks
        num_blocks = to_block - from_block + 1

        # check if progress bar needs initialization
        if show_progress_bar and p_bar is None:
            # local import as tqdm is an optional dependency of this package
            from tqdm import tqdm
            p_bar = tqdm(total=num_blocks)

        # if we already know that the filter range is too large, split it
        if num_blocks > self.filter_block_range:
            results = []
            for filter_start in range(from_block, to_block + 1, self.filter_block_range):
                filter_end = min(filter_start + self.filter_block_range - 1, to_block)
                partial_filter = filter_params.copy()
                partial_filter["fromBlock"] = filter_start
                partial_filter["toBlock"] = filter_end
                results += self.get_logs(partial_filter, show_progress_bar=show_progress_bar, p_bar=p_bar)
            return results

        # get logs
        try:
            events = self._get_logs(filter_params)
        except Exception:
            # if errors should not be retried, still do splitting but not retry if it can not be split further
            if not self.w3.should_retry and num_blocks == 1:
                raise
        else:
            if p_bar is not None:
                p_bar.update(num_blocks)
            return events

        # if directly getting logs did not work, split the filter range and try again
        if num_blocks > 1:
            mid_block = (from_block + to_block) // 2
            left_filter = filter_params.copy()
            left_filter["toBlock"] = mid_block
            right_filter = filter_params.copy()
            right_filter["fromBlock"] = mid_block + 1

            results = []
            results += self.get_logs(left_filter, show_progress_bar=show_progress_bar, p_bar=p_bar)
            results += self.get_logs(right_filter, show_progress_bar=show_progress_bar, p_bar=p_bar)
            return results

        # filter is trying to get a single block, retrying till it works
        assert from_block == to_block and num_blocks == 1
        events = exponential_retry(func_name="get_logs")(self._get_logs)(filter_params)
        if p_bar is not None:
            p_bar.update(num_blocks)
        return events

    def _find_max_filter_range(self):
        current_block = self.block_number
        for filter_range in self.FILTER_RANGES_TO_TRY:
            try:
                # getting logs from the 0 address as it does not emit any logs.
                # This way we can test the maximum allowed filter range without getting back a ton of logs
                result = self._get_logs({
                    "address": "0x0000000000000000000000000000000000000000",
                    "fromBlock": current_block - 5 - filter_range + 1,
                    "toBlock": current_block - 5,
                })
                assert result == []
                return filter_range
            except Exception:
                pass
        raise ValueError("Unable to use eth_getLogs")
