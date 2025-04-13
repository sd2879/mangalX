import os
import time
import asyncio
from dotenv import load_dotenv
from starknet_py.net.account.account import Account
from starknet_py.net.full_node_client import FullNodeClient
from starknet_py.net.signer.stark_curve_signer import KeyPair
from starknet_py.contract import Contract

# Load environment variables
load_dotenv()

# Required environment variables
STARKNET_PRIVATE_KEY = os.getenv("STARKNET_PRIVATE_KEY")
STARKNET_ACCOUNT_ADDRESS = os.getenv("STARKNET_ACCOUNT_ADDRESS")

if not STARKNET_PRIVATE_KEY or not STARKNET_ACCOUNT_ADDRESS:
    raise ValueError("STARKNET_PRIVATE_KEY and STARKNET_ACCOUNT_ADDRESS must be set in .env")

# Starknet Sepolia node URL (same as in your agent.py)
STARKNET_NODE_URL = "https://starknet-sepolia.public.blastapi.io/rpc/v0_7"

# Contract addresses on Sepolia
STRK_ADDRESS = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"
ETH_ADDRESS = "0x049d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7"
JEDISWAP_ROUTER_ADDRESS = "0x69114c42512712a456775de24dfd2119b49eddbaca79ec09937a47b0c38bb04"

# Initialize Starknet client and account
client = FullNodeClient(node_url=STARKNET_NODE_URL)
account = Account(
    address=int(STARKNET_ACCOUNT_ADDRESS, 16),
    client=client,
    key_pair=KeyPair.from_private_key(int(STARKNET_PRIVATE_KEY, 16)),
    chain="starknet_sepolia"
)

async def swap_strk_to_eth(amount_strk: float):
    """
    Swap STRK to ETH on JediSwap (Starknet Sepolia). Uses prepare_call with starknet-py 0.26.1.
    
    Args:
        amount_strk: Amount of STRK to swap (e.g., 0.1 STRK).
    """
    try:
        # Convert STRK amount to wei (18 decimals)
        amount_strk_wei = int(amount_strk * 10**18)

        # Initialize STRK contract
        print(f"Initializing STRK contract at {STRK_ADDRESS}...")
        strk_contract = await Contract.from_address(STRK_ADDRESS, account)

        # Check STRK balance
        balance_result = await strk_contract.functions["balanceOf"].call(account.address)
        print(f"Raw STRK balance result: {balance_result}")

        # Handle balance result
        if hasattr(balance_result, 'balance'):
            balance_tuple = balance_result.balance
        else:
            balance_tuple = balance_result

        if isinstance(balance_tuple, tuple):
            if len(balance_tuple) == 2:
                balance_strk_uint256 = balance_tuple[0] + (balance_tuple[1] << 128)
            elif len(balance_tuple) == 1:
                balance_strk_uint256 = balance_tuple[0]
            else:
                raise ValueError(f"Unexpected STRK balance tuple length: {len(balance_tuple)}")
        else:
            balance_strk_uint256 = int(balance_tuple)

        balance_strk = balance_strk_uint256 / 10**18
        print(f"Your STRK balance: {balance_strk} STRK")
        if balance_strk < amount_strk:
            raise ValueError(f"Insufficient STRK balance: {balance_strk} < {amount_strk}")

        # Initialize ETH contract
        print(f"Initializing ETH contract at {ETH_ADDRESS}...")
        eth_contract = await Contract.from_address(ETH_ADDRESS, account)

        # Check ETH balance before swap
        eth_balance_result = await eth_contract.functions["balanceOf"].call(account.address)
        print(f"Raw ETH balance result: {eth_balance_result}")

        if hasattr(eth_balance_result, 'balance'):
            eth_balance_tuple = eth_balance_result.balance
        else:
            eth_balance_tuple = eth_balance_result

        if isinstance(eth_balance_tuple, tuple):
            if len(eth_balance_tuple) == 2:
                eth_balance_uint256 = eth_balance_tuple[0] + (eth_balance_tuple[1] << 128)
            elif len(eth_balance_tuple) == 1:
                eth_balance_uint256 = eth_balance_tuple[0]
            else:
                raise ValueError(f"Unexpected ETH balance tuple length: {len(eth_balance_tuple)}")
        else:
            eth_balance_uint256 = int(eth_balance_tuple)

        eth_balance_before = eth_balance_uint256 / 10**18
        print(f"ETH balance before swap: {eth_balance_before} ETH")

        # Prepare approve call
        print(f"Approving {amount_strk} STRK for JediSwap router at {JEDISWAP_ROUTER_ADDRESS}...")
        approve_call = strk_contract.functions["approve"].prepare_call(
            spender=int(JEDISWAP_ROUTER_ADDRESS, 16),
            amount=amount_strk_wei
        )

        # Mock price: 1 ETH = 5000 STRK (same as in your agent.py; actual rate may vary)
        amount_eth_min = int((amount_strk / 5000) * 10**18 * 0.95)  # 5% slippage tolerance

        # Initialize JediSwap contract
        print(f"Initializing JediSwap router contract at {JEDISWAP_ROUTER_ADDRESS}...")
        jediswap_contract = await Contract.from_address(JEDISWAP_ROUTER_ADDRESS, account)

        # Debug: Print available functions in the JediSwap contract
        print("Available functions in JediSwap contract:", list(jediswap_contract.functions.keys()))

        # Try different function names
        possible_swap_functions = ["swap_exact_tokens_for_tokens", "swapExactTokensForTokens", "swap"]
        swap_call = None
        for func_name in possible_swap_functions:
            if func_name in jediswap_contract.functions:
                print(f"Using swap function: {func_name}")
                swap_call = jediswap_contract.functions[func_name].prepare_call(
                    amountIn=amount_strk_wei,
                    amountOutMin=amount_eth_min,
                    path=[int(STRK_ADDRESS, 16), int(ETH_ADDRESS, 16)],
                    to=account.address,
                    deadline=int(time.time()) + 3600
                )
                break

        if swap_call is None:
            raise ValueError("No suitable swap function found in JediSwap contract. Check the contract ABI.")

        # Execute transaction
        print("Executing swap transaction...")
        transaction = await account.execute_v3(
            calls=[approve_call, swap_call],
            auto_estimate=True
        )

        # Wait for transaction confirmation
        print(f"Waiting for transaction {hex(transaction.transaction_hash)} to be accepted...")
        receipt = await client.wait_for_tx(transaction.transaction_hash)
        if receipt.status.is_accepted:
            print("Swap successful!")
        else:
            print("Swap failed: Transaction rejected")
            return

        # Check ETH balance after swap
        eth_balance_result = await eth_contract.functions["balanceOf"].call(account.address)
        print(f"Raw ETH balance result after swap: {eth_balance_result}")

        if hasattr(eth_balance_result, 'balance'):
            eth_balance_tuple = eth_balance_result.balance
        else:
            eth_balance_tuple = eth_balance_result

        if isinstance(eth_balance_tuple, tuple):
            if len(eth_balance_tuple) == 2:
                eth_balance_uint256 = eth_balance_tuple[0] + (eth_balance_tuple[1] << 128)
            elif len(eth_balance_tuple) == 1:
                eth_balance_uint256 = eth_balance_tuple[0]
            else:
                raise ValueError(f"Unexpected ETH balance tuple length: {len(eth_balance_tuple)}")
        else:
            eth_balance_uint256 = int(eth_balance_tuple)

        eth_balance_after = eth_balance_uint256 / 10**18
        print(f"ETH balance after swap: {eth_balance_after} ETH")
        print(f"ETH received: {eth_balance_after - eth_balance_before} ETH")

    except Exception as e:
        print(f"Error during swap: {str(e)}")

# Run the script
if __name__ == "__main__":
    # Swap 0.1 STRK to ETH (reduced to minimize liquidity issues)
    asyncio.run(swap_strk_to_eth(amount_strk=0.1))